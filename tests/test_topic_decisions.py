import json
import uuid
from datetime import timedelta

import pytest
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.inference_routing import route_for, total_tokens
from devfeed_core.models import utcnow
from devfeed_core.topic_decision_budget import initial_state, reserve, settle
from devfeed_core.topic_decisions import (
    DecisionDeferred,
    evidence_checks,
    page_snapshot,
    run_decision,
    selected_sources,
)
from test_topic_analysis import pending as pending

IDENTIFIER = str(uuid.uuid4())
TOPIC = {"name": "Example", "slug": "example", "kind": "unclassified", "aliases": ["Unrelated"]}
TEXT = "Example is a developer tool for building reliable applications."


def bundle():
    return {
        "pages": [
            page_snapshot(
                "https://example.com/",
                {
                    "text": TEXT,
                    "content_hash": "public-page-hash",
                    "final_url": "https://example.com/",
                },
            )
        ],
        "failures": [],
    }


def answer(stage, prompt, evidence, *, unsupported=False, out_of_scope=False):
    sentence_id = evidence["pages"][0]["sentences"][0]["id"]
    if stage == "discovery":
        return {"sources": [{"url": "https://example.com/"}], "reason": "Primary documentation"}
    if stage == "draft":
        return {
            "outcome": "ready",
            "kind": "technology",
            "description": TEXT,
            "sentence_ids": [sentence_id],
            "reason": "Exact entity documented",
        }
    item = json.loads(prompt[prompt.index('{"proposal_id"') :])
    return {
        "proposal_id": item["proposal_id"],
        "input_hash": item["input_hash"],
        "verdict": "unsupported" if unsupported or out_of_scope else "supported",
        "relevance": {
            "verdict": "out_of_scope" if out_of_scope else "in_scope",
            "sentence_ids": [sentence_id],
            "reason": "Reviewed direct connection",
        },
        "fields": [
            {
                "field": name,
                "supported": not unsupported,
                "sentence_ids": [sentence_id],
                "reason": "Exact identity and claim inspected",
            }
            for name in ("name", "slug", "kind", "description")
        ],
    }


def harness(*, mutate=None, state=None):
    state = {} if state is None else state
    evidence, calls, fetches = bundle(), [], []

    def call(stage, prompt, schema, **options):
        calls.append((stage, options))
        result = answer(stage, prompt, evidence)
        if mutate:
            mutate(stage, result, options)
        return result

    def fetch(urls):
        fetches.append(urls)
        return evidence

    def run():
        return run_decision(
            IDENTIFIER, TOPIC, state, call=call, save=state.__setitem__, fetch=fetch
        )

    return run, state, calls, fetches


def test_three_call_decision_reuses_evidence_and_omits_optional_metadata():
    run, state, calls, fetches = harness()
    result = run()
    assert result["decision"] == "approved"
    assert [stage for stage, _ in calls] == ["discovery", "draft", "verification"]
    assert calls[0][1] == {"web": True}
    assert len(fetches) == 1
    assert result["topic"]["aliases"] == [] and result["topic"]["facts"] == []
    assert result["topic"]["website_url"] is None
    assert result["sources"][0]["quote"] == TEXT
    assert result["topic_verification"]["check"]["input_hash"] == snapshot_hash(result["topic"])
    assert run() == result and len(calls) == 3 and len(fetches) == 1


def test_invalid_selector_gets_one_escalation_without_search_or_refetch():
    def mutate(stage, result, options):
        if stage == "draft" and not options.get("escalated"):
            result["sentence_ids"] = ["invented"]

    run, _, calls, fetches = harness(mutate=mutate)
    assert run()["decision"] == "approved"
    assert sum(options.get("escalated", False) for _, options in calls) == 1
    assert len(fetches) == 1


def test_uncertain_or_false_semantics_stays_unresolved_after_one_escalation():
    def mutate(stage, result, options):
        if stage == "verification":
            result["verdict"] = "unsupported"
            result["fields"][0]["supported"] = False

    run, state, calls, fetches = harness(mutate=mutate)
    with pytest.raises(DecisionDeferred, match="validation_unresolved"):
        run()
    assert len(calls) == 5 and len(fetches) == 1
    with pytest.raises(DecisionDeferred):
        run()
    assert len(calls) == 5


@pytest.mark.parametrize(
    "identity_supported,has_source,expected",
    [
        (True, True, "rejected"),
        (False, True, None),
        (True, False, None),
    ],
)
def test_scope_rejection_requires_evidenced_identity(identity_supported, has_source, expected):
    def mutate(stage, result, options):
        if stage == "verification":
            result["verdict"] = "unsupported"
            result["relevance"] = {
                "verdict": "out_of_scope",
                "sentence_ids": [bundle()["pages"][0]["sentences"][0]["id"]] if has_source else [],
                "reason": "No developer connection",
            }
            result["fields"][0]["supported"] = identity_supported

    run, _, _, _ = harness(mutate=mutate)
    if expected:
        assert run()["decision"] == expected
    else:
        with pytest.raises(DecisionDeferred):
            run()


def test_expired_evidence_defers_without_spending():
    evidence = bundle()
    evidence["pages"][0]["fetched_at"] = (utcnow() - timedelta(days=2)).isoformat()
    run, _, calls, fetches = harness(state={"evidence": evidence})
    with pytest.raises(DecisionDeferred, match="evidence_expired"):
        run()
    assert not calls and not fetches


def test_quotes_are_bounded_exact_and_checked_from_the_same_snapshot():
    evidence = bundle()
    sentence = evidence["pages"][0]["sentences"][0]
    assert selected_sources(evidence, [sentence["id"]])[0]["quote"] in TEXT
    assert all(
        check["status"] == "verified" for check in evidence_checks(evidence)["checks"].values()
    )
    with pytest.raises(ValueError):
        selected_sources(evidence, [sentence["id"], sentence["id"]])


def test_reservations_fail_closed_and_unknown_usage_is_not_free():
    state = reserve(initial_state(get_settings()), "discovery", web=True)
    with pytest.raises(DecisionDeferred, match="interrupted_call"):
        reserve(state, "discovery", web=True)
    settled = settle(
        state, 0, usage={}, searches=0, seconds=1, model="test", effort="low", succeeded=False
    )
    assert settled["calls"][0]["charged_tokens"] == state["limits"]["call_tokens"]
    assert settled["calls"][0]["charged_searches"] == state["limits"]["searches"]
    assert settled["calls"][0]["usage_known"] is False
    assert (
        settle(
            settled,
            0,
            usage={"totalTokens": 1},
            searches=0,
            seconds=1,
            model="test",
            effort="low",
            succeeded=True,
        )
        == settled
    )


@pytest.mark.parametrize(
    "limit,reason",
    [
        ("calls", "call_budget"),
        ("tokens", "token_budget"),
        ("seconds", "time_budget"),
        ("searches", "search_budget"),
        ("escalations", "escalation_budget"),
    ],
)
def test_limits_survive_metadata_and_stage_changes(limit, reason):
    state = initial_state(get_settings())
    state["limits"][limit] = 0
    state["draft"] = {"kind": "changed"}
    with pytest.raises(DecisionDeferred, match=reason):
        reserve(state, "verification", escalated=True, web=True)


def test_tiers_and_token_accounting():
    settings = get_settings().model_copy(update={"ai_tiered_routing_enabled": True})
    assert route_for(settings, "article_analysis").effort == "low"
    assert route_for(settings, "topic_discovery").model == "gpt-5.6-luna"
    assert route_for(settings, "topic_verification").effort == "medium"
    assert route_for(settings, "topic_draft", quality_failure=True).model == "gpt-5.6-terra"
    assert (
        total_tokens(
            {
                "inputTokens": 100,
                "cachedInputTokens": 80,
                "outputTokens": 20,
                "reasoningOutputTokens": 10,
            }
        )
        == 120
    )


@pytest.mark.integration
@pytest.mark.parametrize("ambiguous", [False, True])
def test_worker_finishes_or_defers_once(database, pending, monkeypatch, ambiguous):
    from devfeed_aggregator import topic_analysis_tasks, topic_decision_tasks
    from devfeed_core.models import TopicAnalysisJob, TopicDecisionRun, TopicProposal
    from devfeed_core.research_verification import schedule_verification
    from devfeed_core.topic_decision_budget import schedule_decisions
    from devfeed_core.topic_decision_metrics import decision_metrics
    from devfeed_core.topic_remediation import schedule_topic_corrections
    from sqlalchemy import select

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    monkeypatch.setenv("DEVFEED_AI_BOUNDED_TOPICS_ENABLED", "true")
    get_settings.cache_clear()
    evidence, calls = bundle(), []

    class Client:
        def __init__(self, settings):
            self.usage, self.web_search_count = {"inputTokens": 100, "outputTokens": 20}, 0

        def complete(self, prompt, schema, **options):
            stage = {
                "DiscoveryResult": "discovery",
                "MinimalDraft": "draft",
                "EvidenceVerdict": "verification",
            }[schema["title"]]
            calls.append(stage)
            return answer(stage, prompt, evidence, unsupported=ambiguous)

    monkeypatch.setattr(topic_decision_tasks, "CodexClient", Client)
    monkeypatch.setattr(topic_decision_tasks, "fetch_bundle", lambda urls: evidence)
    assert schedule_decisions(database) == 1
    with database() as session:
        job_id = session.scalar(
            select(TopicAnalysisJob.id).where(TopicAnalysisJob.proposal_id == uuid.UUID(pending))
        )
    topic_analysis_tasks._analyze(job_id)
    with database() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        run = session.get(TopicDecisionRun, proposal.id)
        job = session.get(TopicAnalysisJob, job_id)
        assert proposal.status == ("pending" if ambiguous else "approved")
        assert run.status == ("deferred" if ambiguous else "decided")
        assert len(run.state["calls"]) == (5 if ambiguous else 3)
        assert job.usage["inputTokens"] == 100 * len(calls)
        stats = decision_metrics(session, utcnow() - timedelta(hours=1), utcnow())
        assert stats.calls == len(calls)
        assert stats.deferred == int(ambiguous)
        assert stats.tokens == len(calls) * 120
    assert schedule_topic_corrections(database) == 0
    assert schedule_verification(database) == 0
    topic_analysis_tasks._analyze(job_id)
    assert len(calls) == (5 if ambiguous else 3)


@pytest.mark.integration
def test_explicit_budget_grant_preserves_spend_and_audits_metadata_change(database, pending):
    from devfeed_core.models import TopicDecisionRun, TopicProposal
    from devfeed_core.topic_decision_budget import grant_budget

    state = reserve(initial_state(get_settings()), "discovery", web=True)
    identifier = uuid.UUID(pending)
    with database.begin() as session:
        proposal = session.get(TopicProposal, identifier)
        old_hash = snapshot_hash(proposal.proposed)
        session.add(
            TopicDecisionRun(
                proposal_id=identifier,
                status="deferred",
                input_hash=old_hash,
                state=state,
                reason="interrupted_call_requires_review",
            )
        )
        proposal.proposed = {**proposal.proposed, "description": "An edited description"}
    with database.begin() as session:
        grant = grant_budget(
            session,
            identifier,
            calls=3,
            tokens=16000,
            note="Capacity restored",
            actor="operator-test",
        )
        run = session.get(TopicDecisionRun, identifier)
        assert run.state["calls"][0]["charged_tokens"] == state["limits"]["call_tokens"]
        assert run.state["calls"][0]["status"] == "unknown"
        assert run.input_hash != old_hash and run.status == "active"
        assert run.state["grants"][0]["actor"] == "operator-test"
        assert grant["limits"]["calls"] == state["limits"]["calls"] + 3


@pytest.mark.integration
def test_relationship_budget_counts_started_calls_and_prioritizes_topics(
    database, pending, monkeypatch
):
    from devfeed_core.inference_usage import call_id
    from devfeed_core.models import TopicDecisionRun, TopicProposal
    from devfeed_core.topic_decision_budget import admit_relationship_call

    monkeypatch.setenv("DEVFEED_AI_BOUNDED_TOPICS_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_RELATIONSHIP_DAILY_CALL_BUDGET", "1")
    get_settings.cache_clear()
    values = dict(
        id=call_id(),
        started_at=utcnow(),
        finished_at=None,
        operation="relationship_research",
        job_id=None,
        attempt=1,
        reason="test",
        model="test",
        reasoning_effort="medium",
        request_hash="0" * 64,
        status="running",
        tokens={},
        web_searches=0,
        duration_ms=0,
    )
    assert admit_relationship_call(values) is False
    with database.begin() as session:
        proposal = session.get(TopicProposal, uuid.UUID(pending))
        session.add(
            TopicDecisionRun(
                proposal_id=proposal.id,
                input_hash=snapshot_hash(proposal.proposed),
                status="deferred",
                state=initial_state(get_settings()),
            )
        )
    assert admit_relationship_call(values) is True
    assert admit_relationship_call({**values, "id": call_id()}) is False


@pytest.mark.integration
def test_charts_include_source_relevance_and_do_not_double_count_subtokens(database):
    from devfeed_core.inference_metrics import inference_charts
    from devfeed_core.models import InferenceCall

    now = utcnow()
    with database.begin() as session:
        session.add(
            InferenceCall(
                started_at=now,
                finished_at=now,
                operation="source_relevance",
                model="gpt-5.6-luna",
                reasoning_effort="low",
                request_hash="0" * 64,
                status="returned",
                tokens={
                    "inputTokens": 100,
                    "cachedInputTokens": 80,
                    "outputTokens": 20,
                    "reasoningOutputTokens": 10,
                },
                web_searches=0,
                duration_ms=500,
            )
        )
    with database() as session:
        data = inference_charts(session, now - timedelta(days=1), now)
    point = data.activity[0]
    assert point.operation == "source_relevance"
    assert point.input_tokens + point.output_tokens == 120
    assert point.cached_input_tokens == 80 and point.reasoning_tokens == 10
    assert len(data.topic_activity) == 2

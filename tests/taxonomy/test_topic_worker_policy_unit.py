"""Research deliveries honor current drafts, automation policy and lease ownership.

Repository reads and external services are supplied boundaries. Transaction depth
checks prove that this orchestrator releases its context before transport, without
claiming database rollback, exclusion or locking correctness.
"""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import topic_analysis_tasks as worker
from devfeed_aggregator.codex_client import AnalysisError
from devfeed_core import analysis, article_topic_policy, topic_analysis, topic_decision_budget
from devfeed_core.models import TopicAnalysisJob, TopicProposal
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key

from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import ACTOR, NOW, proposal, relationship_job, topic


def research_output(**changes):
    return topic_analysis.TopicResearchResult(
        outcome="ready",
        kind=None,
        description="React components are JavaScript functions that return markup.",
        aliases=[],
        keywords=[],
        website_url=None,
        logo_url=None,
        facts=[],
        sources=[
            {
                "url": "https://react.dev/learn",
                "title": "React documentation",
                "quote": "React components are JavaScript functions that return markup.",
                "fields": ["description"],
            }
        ],
        reasons=[],
        **changes,
    ).model_dump(mode="json")


@pytest.fixture
def delivery(monkeypatch):
    current = proposal()
    job = topic_analysis._new_job(current, ACTOR)
    job.id, job.status, job.attempts = uuid.uuid4(), "queued", 0
    job.created_at, job.available_at = NOW - timedelta(minutes=3), NOW - timedelta(minutes=2)
    job.usage, job.result = {}, {}
    job.input_snapshot = copy.deepcopy(job.input_snapshot)
    v = SimpleNamespace(
        proposal=current,
        job=job,
        session=Mock(),
        factory=Mock(),
        depth=0,
        now=NOW,
        owned=True,
        settings=configuration(
            ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=False
        ),
        output=research_output(),
        error=None,
        after_inference=lambda: None,
    )

    @contextmanager
    def transaction():
        v.depth += 1
        try:
            yield v.session
        finally:
            v.depth -= 1

    v.factory.begin.side_effect = transaction
    v.session.scalar.side_effect = lambda statement: (
        v.job if statement.column_descriptions[0]["entity"] is TopicAnalysisJob else v.proposal
    )
    v.session.get.side_effect = lambda model, identifier: (
        v.proposal if model is TopicProposal and identifier == v.job.proposal_id else None
    )
    monkeypatch.setattr(worker, "get_settings", lambda: v.settings)
    monkeypatch.setattr(article_topic_policy, "get_settings", lambda: v.settings)
    monkeypatch.setattr(worker, "session_factory", lambda: v.factory)
    for module in (worker, analysis, topic_analysis):
        monkeypatch.setattr(module, "utcnow", lambda: v.now)
    monkeypatch.setattr(worker, "owned_job", lambda *_: v.job if v.owned else None)
    for name in (
        "auto_approve_research",
        "queue_relationships_after_enrichment",
        "resume_relationships_after_superseded",
        "record_attempt",
        "lock_topics",
    ):
        setattr(v, name, Mock())
        monkeypatch.setattr(worker, name, getattr(v, name))
    v.client = SimpleNamespace(web_search_count=1, usage={"inputTokens": 120, "outputTokens": 30})

    def complete(_prompt, _schema, *, allow_web_search):
        assert v.depth == 0 and allow_web_search is True
        v.after_inference()
        if v.error:
            raise v.error
        return v.output

    v.client.complete = Mock(side_effect=complete)
    v.clients = Mock(return_value=v.client)
    monkeypatch.setattr(worker, "CodexClient", v.clients)

    def verify(citations):
        assert v.depth == 0
        return {
            "version": VERIFICATION_VERSION,
            "checks": {
                citation_key(url, quote): {"status": "verified", "quote": quote, "final_url": url}
                for url, quote in citations
            },
        }

    v.verify = Mock(side_effect=verify)
    monkeypatch.setattr(worker, "verify_citations", v.verify)
    v.pause = Mock(return_value=75)
    monkeypatch.setattr(worker, "safe_pause", v.pause)
    v.allowance = Mock(return_value=True)
    monkeypatch.setattr(topic_decision_budget, "relationship_allowance", v.allowance)
    return v


@pytest.mark.parametrize(
    "guard",
    [
        "missing",
        "running",
        "delayed",
        "article-paused",
        "ai-off",
        "reviewed",
        "changed-draft",
    ],
)
def test_worker_refuses_ineligible_delivery_before_external_research(delivery, guard):
    v = delivery
    if guard == "missing":
        v.job = None
    elif guard == "running":
        v.job.status, v.job.attempts = "running", 1
        v.job.lease_token, v.job.lease_until = uuid.uuid4(), NOW + timedelta(minutes=5)
    elif guard == "delayed":
        v.job.available_at = NOW + timedelta(minutes=1)
    elif guard == "article-paused":
        v.proposal.origin = "article_enrichment"
        v.settings = configuration(ai_enabled=True, article_topic_proposals_enabled=False)
    elif guard == "ai-off":
        v.settings = configuration(ai_enabled=False)
    elif guard == "reviewed":
        v.proposal.status, v.proposal.reviewed_at, v.proposal.reviewed_by = "rejected", NOW, ACTOR
    elif guard == "changed-draft":
        v.proposal.proposed = {**v.proposal.proposed, "description": "An editor's correction."}
    worker._analyze(uuid.uuid4() if v.job is None else v.job.id)
    v.clients.assert_not_called()
    v.auto_approve_research.assert_not_called()
    if guard == "article-paused":
        assert v.job.available_at == NOW + worker.RETRY_DELAY
        assert v.job.status == "queued" and v.job.attempts == 0
    elif guard == "ai-off":
        assert v.job.status == "failed" and v.job.error == "ai_not_configured"
    elif guard in {"reviewed", "changed-draft"}:
        assert v.job.status == "succeeded" and v.job.outcome == "superseded"
        assert v.job.attempts == 0 and v.job.lease_token is None


def test_success_records_verified_enrichment_and_hands_it_to_independent_review(delivery):
    v = delivery
    before = copy.deepcopy(v.job.input_snapshot)
    worker._analyze(v.job.id)
    assert v.job.status == "succeeded" and v.job.outcome == "enriched"
    assert v.job.attempts == 1 and v.job.model == v.settings.codex_model
    assert v.job.lease_token is None and v.job.lease_until is None
    assert v.job.finished_at == NOW
    assert v.proposal.proposed["description"] == v.output["description"]
    assert v.proposal.status == "pending" and v.proposal.reviewed_by is None
    assert v.job.input_snapshot == before
    assert v.job.result["applied_input_hash"] == analysis.snapshot_hash(v.proposal.proposed)
    assert v.job.result["web_searches"] == 1
    [evidence] = v.proposal.evidence
    assert evidence["analysis_id"] == str(v.job.id)
    assert evidence["fields"] == ["description"]
    v.auto_approve_research.assert_called_once_with(v.session, v.job)
    v.queue_relationships_after_enrichment.assert_called_once_with(v.session, v.proposal, v.job)
    v.record_attempt.assert_called_once()


@pytest.mark.parametrize("change", ["lost-lease", "removed-draft", "reviewed", "edited"])
def test_changes_during_transport_cannot_overwrite_current_editorial_state(delivery, change):
    v = delivery
    original = copy.deepcopy(v.proposal.proposed)

    def changed():
        if change == "lost-lease":
            from devfeed_core.job_lifecycle import start_job

            v.now = NOW + timedelta(seconds=301)
            v.job.status, v.job.lease_token, v.job.lease_until = "queued", None, None
            v.job.available_at = v.now
            start_job(v.job, v.now, 300)
            v.owned = False
        elif change == "removed-draft":
            # The FK cascades the durable job; its old in-memory claim is no
            # longer returned by the ownership repository boundary.
            v.proposal, v.owned = None, False
        elif change == "reviewed":
            v.proposal.status, v.proposal.reviewed_at, v.proposal.reviewed_by = (
                "rejected",
                NOW,
                ACTOR,
            )
        else:
            v.proposal.proposed = {**v.proposal.proposed, "description": "Operator-owned text."}

    v.after_inference = changed
    worker._analyze(v.job.id)
    if change in {"lost-lease", "removed-draft"}:
        assert v.job.result == {}
        if change == "lost-lease":
            assert v.job.status == "running" and v.proposal.proposed == original
        v.auto_approve_research.assert_not_called()
    else:
        assert v.job.outcome == "superseded"
        if change == "edited":
            assert v.proposal.proposed["description"] == "Operator-owned text."
        if change == "reviewed":
            assert v.proposal.status == "rejected" and v.proposal.proposed == original
    v.queue_relationships_after_enrichment.assert_not_called()
    v.record_attempt.assert_called_once()


@pytest.mark.parametrize("failure", ["invalid", "dependency", "capacity", "lost-lease"])
def test_transport_failure_preserves_draft_and_uses_the_correct_retry_policy(delivery, failure):
    v = delivery
    before = copy.deepcopy(v.proposal.proposed)
    v.error = {
        "invalid": ValueError("malformed upstream result"),
        "dependency": OSError("transport unavailable"),
        "capacity": AnalysisError("codex_server_overloaded", retry_after=75),
        "lost-lease": OSError("transport unavailable"),
    }[failure]
    if failure == "lost-lease":
        # A cancelled/deleted row is absent from the ownership repository.
        v.after_inference = lambda: setattr(v, "owned", False)
    worker._analyze(v.job.id)
    assert v.proposal.proposed == before and v.proposal.status == "pending"
    v.auto_approve_research.assert_not_called()
    v.queue_relationships_after_enrichment.assert_not_called()
    if failure == "lost-lease":
        assert v.job.status == "running" and v.job.error is None
    else:
        assert v.job.status == "queued" and v.job.attempts == 1
        assert (
            v.job.error
            == {
                "invalid": "invalid_analysis_result",
                "dependency": "analysis_dependency_failure",
                "capacity": "codex_server_overloaded",
            }[failure]
        )
        assert v.job.available_at > NOW
        if failure == "capacity":
            v.pause.assert_called_once_with(75, reason="codex_server_overloaded")
            assert v.job.available_at == NOW + timedelta(seconds=75)
            assert v.job.usage["capacity_deferrals"] == 1
        else:
            v.pause.assert_not_called()
    if failure != "lost-lease":
        assert v.job.lease_token is None and v.job.lease_until is None
    v.record_attempt.assert_called_once()


@pytest.mark.parametrize("mode", ["deferred", "disabled-during-transport"])
def test_correction_cannot_apply_after_full_automation_is_disabled(delivery, mode):
    from tests.taxonomy.test_taxonomy_correction_admission_unit import correction_attempt

    v = delivery
    v.job = correction_attempt(v.proposal)
    v.job.status, v.job.attempts, v.job.finished_at, v.job.error = "queued", 0, None, None
    v.output.update(proposal_id=str(v.proposal.id), input_hash=v.job.input_hash)
    original = copy.deepcopy(v.proposal.proposed)
    manual = configuration(ai_enabled=True, full_automation=False)
    if mode == "deferred":
        v.settings = manual
    else:
        v.after_inference = lambda: setattr(v, "settings", manual)
    worker._analyze(v.job.id)
    assert v.proposal.proposed == original and v.proposal.status == "pending"
    if mode == "deferred":
        assert v.job.status == "queued" and v.job.attempts == 0
        assert v.job.available_at == NOW + worker.RETRY_DELAY
        v.clients.assert_not_called()
    else:
        assert v.job.status == "succeeded" and v.job.outcome == "superseded"


def test_bounded_metadata_delivery_dispatches_with_its_claimed_token(delivery, monkeypatch):
    from devfeed_aggregator import topic_decision_tasks

    v = delivery
    v.settings = configuration(
        ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=True
    )
    execute = Mock()
    monkeypatch.setattr(topic_decision_tasks, "execute_decision", execute)
    worker._analyze(v.job.id)
    execute.assert_called_once_with(
        v.factory,
        v.job.id,
        v.job.lease_token,
        v.proposal.id,
        v.job.input_snapshot["topic"],
    )
    assert v.job.status == "running" and v.job.attempts == 1
    assert v.job.lease_token is not None and v.job.lease_until == NOW + timedelta(minutes=5)
    v.clients.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_branding_checks_the_fetched_image_before_saving_any_metadata(
    delivery, monkeypatch, failure
):
    v = delivery
    from devfeed_core import topic_branding

    active = topic(
        kind="library",
        website_url="https://react.dev/",
        description="A JavaScript library for user interfaces.",
    )
    v.proposal = proposal(active, origin="ai_analysis", source_name=topic_branding.VERSION)
    queued = topic_analysis._new_job(v.proposal, topic_branding.ACTOR)
    queued.id, queued.status, queued.attempts = uuid.uuid4(), "queued", 0
    queued.created_at, queued.available_at = NOW - timedelta(minutes=3), NOW - timedelta(minutes=2)
    queued.usage, queued.result = {}, {}
    queued.input_snapshot = copy.deepcopy(queued.input_snapshot)
    v.job = queued
    v.output["description"] = None
    v.output["logo_url"] = "https://react.dev/logo.png"
    v.output["sources"] = [
        {
            "url": "https://react.dev/community/brand",
            "title": "React branding",
            "quote": "React logo",
            "fields": ["logo_url"],
        }
    ]
    logo = Mock(side_effect=OSError("image fetch unavailable") if failure else None)
    monkeypatch.setattr(worker, "fetch_topic_logo", logo)
    worker._analyze(v.job.id)
    logo.assert_called_once_with(v.output["logo_url"])
    assert v.client.reason == worker.BRANDING_PROMPT_VERSION
    assert (v.client.token_limit, v.client.search_limit) == (16000, 2)
    if failure:
        assert v.proposal.proposed["logo_url"] is None
        assert v.job.status == "queued" and v.job.error == "analysis_dependency_failure"
        v.verify.assert_not_called()
    else:
        assert (
            v.job.outcome == "enriched" and v.proposal.proposed["logo_url"] == v.output["logo_url"]
        )


@pytest.mark.parametrize(
    "guard", ["budget", "budget-after-claim", "stale", "only-focal", "valid", "outside-catalog"]
)
def test_relationship_delivery_refreshes_eligible_catalog_without_mutating_stored_input(
    delivery,
    monkeypatch,
    guard,
):
    v = delivery
    focal, peer = topic(kind="library"), topic("JavaScript", kind="language")
    v.job = relationship_job(focal, peer, status="queued")
    saved = copy.deepcopy(v.job.input_snapshot)
    v.session.scalars.return_value = [focal] if guard == "only-focal" else [focal, peer]
    v.allowance.return_value = guard != "budget"
    if guard == "budget-after-claim":
        v.settings = configuration(
            ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=True
        )
        v.error = AnalysisError("relationship_budget_deferred")
    monkeypatch.setattr(worker, "research_current", lambda *_: guard != "stale")
    apply = Mock(return_value=("enriched", [str(uuid.uuid4())]))
    monkeypatch.setattr(worker, "apply_relationship_research", apply)
    v.output = {
        "relationships": [
            {
                "topic_id": str(focal.id),
                "related_topic_id": str(uuid.uuid4() if guard == "outside-catalog" else peer.id),
                "relation": "uses_language",
                "explanation": "React uses JavaScript for components.",
                "evidence_url": "https://react.dev/learn",
                "evidence_title": "React documentation",
                "evidence_quote": "React components are JavaScript functions that return markup.",
            }
        ],
        "reasons": [],
    }
    worker._analyze(v.job.id)
    assert v.job.input_snapshot == saved
    if guard == "budget":
        assert v.job.status == "queued" and v.job.attempts == 0
        assert v.job.available_at == NOW + worker.RETRY_DELAY
    elif guard == "budget-after-claim":
        assert v.job.status == "queued" and v.job.attempts == 0
        assert v.job.available_at == NOW + worker.RETRY_DELAY
        assert v.job.lease_token is None and v.job.lease_until is None
        apply.assert_not_called()
    elif guard in {"stale", "only-focal"}:
        assert v.job.outcome == "superseded" and v.job.attempts == 0
    elif guard == "outside-catalog":
        assert v.job.status == "queued" and v.job.error == "invalid_analysis_result"
        apply.assert_not_called()
    else:
        assert v.job.outcome == "enriched" and v.job.result["proposal_ids"] == apply.return_value[1]
        assert v.client.operation == "relationship_research"
        apply.assert_called_once()
    if guard in {"budget", "stale", "only-focal"}:
        v.clients.assert_not_called()
    if guard == "stale":
        v.resume_relationships_after_superseded.assert_called_once_with(v.session, v.job)


def test_public_worker_entrypoint_logs_and_propagates_runtime_failures(monkeypatch):
    identifier = uuid.uuid4()
    run = Mock(side_effect=OSError("database unavailable"))
    monkeypatch.setattr(worker, "_analyze", run)
    with pytest.raises(OSError, match="database unavailable"):
        worker.analyze_topic(str(identifier))
    run.assert_called_once_with(identifier)

"""Verification retries retain evidence and use independent identity-bound verdicts."""

import copy
import uuid
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import research_verification_tasks as tasks
from devfeed_core import analysis, research_verification, topic_verification
from devfeed_core.config import Settings
from devfeed_core.inference_usage import context as inference_metadata
from devfeed_core.job_lifecycle import start_job
from devfeed_core.models import ResearchVerificationJob
from devfeed_core.relationship_verification import VERSION, checked_verdicts, verification_input
from devfeed_core.research_evidence import VERIFICATION_VERSION, citation_key
from test_relationship_policy_unit import researched as researched
from test_research_input_guards_unit import NOW
from test_research_input_guards_unit import clock as clock
from test_research_input_guards_unit import completed as completed
from test_topic_verification import SOURCE
from test_topic_verification import output as topic_output


def evidence(citations, **values):
    return {
        "version": VERIFICATION_VERSION,
        "checks": {
            citation_key(url, quote): {"url": url, "quote": quote, "status": "verified", **values}
            for url, quote in citations
        },
    }


def context(*, citations=(), proposals=(), metadata=None, **values):
    return tasks.VerificationContext(
        **{
            "identifier": uuid.uuid4(),
            "token": uuid.uuid4(),
            "attempt": 1,
            "citations": list(citations),
            "proposals": list(proposals),
            "metadata": metadata,
            "verification": {},
            "semantic": {},
            "topic_semantic": {},
            **values,
        }
    )


def relationship_output(item, verdict="supported"):
    return {
        "decisions": [
            {
                "proposal_id": item["proposal_id"],
                "input_hash": item["input_hash"],
                "verdict": verdict,
                "exact_entities": True,
                "direct_relationship": True,
                "correct_type_and_direction": True,
                "evidence_supports_claim": True,
                "scope_matches": True,
                "reason": "The official documentation establishes the exact relationship.",
            }
        ]
    }


@pytest.fixture
def service():
    client = SimpleNamespace(complete=Mock(), model="verification-model", retry_after=0)
    return tasks.ResearchVerificationService(
        Settings(
            _env_file=None,
            ai_enabled=True,
            auto_approve_topics=True,
            codex_app_server_url="ws://127.0.0.1:9901/",
            codex_model="verification-model",
        ),
        Mock(),
        Mock(return_value=client),
        Mock(side_effect=evidence),
        lambda: NOW,
    )


def test_retry_fetches_only_missing_or_recoverable_citations_and_does_not_mutate_receipts(service):
    citations = [
        (f"https://docs.example/{i}", f"Verbatim documented statement {i}.") for i in range(4)
    ]
    previous = evidence(citations[:3])
    previous["checks"][citation_key(*citations[1])].update(
        status="unverified", reason="quote_not_found", retryable=False
    )
    previous["checks"][citation_key(*citations[2])].update(
        status="unverified", reason="http_error", retryable=True, retry_after=45
    )
    value = context(citations=citations, verification=previous)
    before = copy.deepcopy(value)
    result = service.evaluate(value, tasks.ModelAttempt(0))

    service.citation_checker.assert_called_once_with(citations[2:])
    assert result.citations_retried == 2 and result.reason is None
    assert result.verification["checks"][citation_key(*citations[1])]["reason"] == "quote_not_found"
    assert (
        result.verification["checks"][citation_key(*citations[0])]
        == previous["checks"][citation_key(*citations[0])]
    )
    assert value == before
    service.client_factory.assert_not_called()


def test_unversioned_evidence_cannot_suppress_fresh_transport_validation(service):
    citations = [("https://docs.example/project", "Primary source statement.")]
    previous = {**evidence(citations), "version": "retired-policy"}
    result = service.evaluate(
        context(citations=citations, verification=previous), tasks.ModelAttempt(0)
    )
    service.citation_checker.assert_called_once_with(citations)
    assert result.citations_retried == 1 and result.verification["version"] == VERIFICATION_VERSION


@pytest.mark.parametrize(
    "prior,expected_calls",
    [
        ("missing", 1),
        ("supported", 0),
        ("unsupported", 0),
        ("uncertain", 1),
        ("stale", 1),
        ("unverified", 0),
    ],
)
def test_relationship_semantics_run_only_for_verified_new_or_uncertain_identity(
    service, researched, prior, expected_calls
):
    proposal, _ = researched
    item = verification_input(proposal)
    citations = [(item["evidence_url"], item["evidence_quote"])]
    semantic = {}
    if prior not in {"missing", "unverified"}:
        output = relationship_output(
            item,
            "uncertain"
            if prior == "uncertain"
            else "unsupported"
            if prior == "unsupported"
            else "supported",
        )
        semantic = checked_verdicts(output, [item])
        if prior == "stale":
            old = copy.deepcopy(proposal)
            old.explanation = "An older research explanation for this exact relationship."
            semantic["checks"][item["proposal_id"]]["input_hash"] = verification_input(old)[
                "input_hash"
            ]
    verification = evidence(citations)
    if prior == "unverified":
        verification["checks"][citation_key(*citations[0])].update(
            status="unverified", reason="quote_not_found", retryable=False
        )
    service.client_factory.return_value.complete.return_value = relationship_output(item)
    value = context(
        citations=citations, proposals=[item], verification=verification, semantic=semantic
    )
    before = copy.deepcopy(value)

    result = service.evaluate(value, tasks.ModelAttempt(0))

    assert result.relationships_checked == expected_calls
    assert service.client_factory.call_count == expected_calls
    assert result.reason is None and value == before
    if expected_calls:
        check = result.semantic["checks"][item["proposal_id"]]
        assert check["input_hash"] == item["input_hash"] and check["verdict"] == "supported"
        assert check["model"] == "verification-model" and check["checked_at"] == NOW.isoformat()
        assert service.client_factory.return_value.complete.call_args.kwargs == {
            "allow_web_search": True
        }


def test_uncertain_relationship_remains_retryable_despite_verified_quote(service, researched):
    proposal, _ = researched
    item = verification_input(proposal)
    citations = [(item["evidence_url"], item["evidence_quote"])]
    service.client_factory.return_value.complete.return_value = relationship_output(
        item, "uncertain"
    )
    result = service.evaluate(
        context(citations=citations, proposals=[item], verification=evidence(citations)),
        tasks.ModelAttempt(0),
    )
    assert result.reason == "relationship_verification_uncertain"
    assert result.verification == evidence(citations) and result.relationships_checked == 1


@pytest.mark.parametrize("branding", [False, True])
@pytest.mark.parametrize("new_identity_source", [False, True])
def test_metadata_checks_new_identity_sources_once_and_applies_branding_budget(
    service, completed, branding, new_identity_source
):
    job, proposal = completed
    metadata = topic_verification.verification_input(proposal)
    if branding:
        metadata["branding"] = True
    citations = [(source["url"], source["quote"]) for source in job.result["sources"]]
    output = topic_output(metadata)
    identity_citation = (
        ("https://example.com/reference", "Example is a developer technology.")
        if new_identity_source
        else (SOURCE["url"], SOURCE["quote"])
    )
    output["sources"] = [{"url": identity_citation[0], "quote": identity_citation[1]}]
    service.client_factory.return_value.complete.return_value = output
    value = context(citations=citations, metadata=metadata, verification=evidence(citations))
    before = copy.deepcopy(value)

    result = service.evaluate(value, tasks.ModelAttempt(0))

    assert result.topic_checked and result.reason is None
    assert result.topic_semantic["check"]["input_hash"] == metadata["input_hash"]
    assert result.topic_semantic["model"] == "verification-model"
    assert value == before
    expected = [] if identity_citation in citations else [identity_citation]
    if expected:
        service.citation_checker.assert_called_once_with(expected)
    else:
        service.citation_checker.assert_not_called()
    assert result.citations_retried == len(expected)
    client = service.client_factory.return_value
    if branding:
        assert (client.token_limit, client.search_limit) == (16000, 2)
    assert client.complete.call_args.kwargs == {"allow_web_search": True}


def test_unchanged_supported_topic_verdict_is_reused_while_identity_transport_recovers(
    service, completed
):
    job, proposal = completed
    metadata = topic_verification.verification_input(proposal)
    identity = ("https://example.com/reference", "Example is a developer technology.")
    output = topic_output(metadata)
    output["sources"] = [{"url": identity[0], "quote": identity[1]}]
    semantic = topic_verification.checked_verdict(output, metadata)
    research_citations = [(source["url"], source["quote"]) for source in job.result["sources"]]
    citations = [*research_citations, identity]
    existing = evidence(research_citations)
    existing["checks"].update(evidence([identity], status="unverified", retryable=True)["checks"])
    result = service.evaluate(
        context(
            citations=citations, metadata=metadata, verification=existing, topic_semantic=semantic
        ),
        tasks.ModelAttempt(0),
    )
    assert not result.topic_checked and result.topic_semantic == semantic and result.reason is None
    service.client_factory.assert_not_called()
    service.citation_checker.assert_called_once_with([identity])
    assert result.verification["checks"][citation_key(*identity)]["status"] == "verified"


def test_uncertain_topic_verdict_is_replaced_and_not_used_as_its_own_permanent_gate(
    service, completed
):
    job, proposal = completed
    metadata = topic_verification.verification_input(proposal)
    uncertain = topic_output(metadata)
    uncertain["verdict"] = uncertain["relevance"]["verdict"] = "uncertain"
    previous = topic_verification.checked_verdict(uncertain, metadata)
    service.client_factory.return_value.complete.return_value = topic_output(metadata)
    citations = [(source["url"], source["quote"]) for source in job.result["sources"]]
    result = service.evaluate(
        context(
            citations=citations,
            metadata=metadata,
            verification=evidence(citations),
            topic_semantic=previous,
        ),
        tasks.ModelAttempt(0),
    )
    assert result.topic_checked and result.topic_semantic["check"]["verdict"] == "supported"
    assert result.reason is None and previous["check"]["verdict"] == "uncertain"


def test_uncertain_topic_result_keeps_review_open(service, completed):
    job, proposal = completed
    metadata = topic_verification.verification_input(proposal)
    output = topic_output(metadata)
    output["verdict"] = output["relevance"]["verdict"] = "uncertain"
    service.client_factory.return_value.complete.return_value = output
    citations = [(source["url"], source["quote"]) for source in job.result["sources"]]
    result = service.evaluate(
        context(citations=citations, metadata=metadata), tasks.ModelAttempt(0)
    )
    assert result.reason == "topic_verification_uncertain" and result.topic_checked


def test_retry_after_uses_longest_transport_delay_and_blocks_model_until_quotes_verify(
    service, completed
):
    _, proposal = completed
    citations = [
        ("https://docs.example/a", "A documented statement."),
        ("https://docs.example/b", "Another documented statement."),
    ]
    fresh = evidence(citations, status="unverified", retryable=True)
    fresh["checks"][citation_key(*citations[0])]["retry_after"] = 30
    fresh["checks"][citation_key(*citations[1])]["retry_after"] = 300
    service.citation_checker.return_value = fresh
    service.citation_checker.side_effect = None
    result = service.evaluate(
        context(citations=citations, metadata=topic_verification.verification_input(proposal)),
        tasks.ModelAttempt(0),
    )
    assert result.reason == "citation_temporarily_unavailable" and result.retry_after == 300
    assert not result.topic_checked and result.citations_retried == 2
    service.client_factory.assert_not_called()


@pytest.mark.parametrize("lane", ["metadata", "relationships"])
@pytest.mark.parametrize("failure", ["invalid", "capacity"])
def test_model_failures_retain_verified_quotes_and_pause_capacity_across_workers(
    service, completed, researched, monkeypatch, lane, failure
):
    pause = Mock(return_value=600)
    monkeypatch.setattr(tasks, "safe_pause", pause)
    if lane == "metadata":
        job, proposal = completed
        citations = [(source["url"], source["quote"]) for source in job.result["sources"]]
        value = context(
            citations=citations,
            metadata=topic_verification.verification_input(proposal),
            verification=evidence(citations),
        )
    else:
        proposal, _ = researched
        item = verification_input(proposal)
        citations = [(item["evidence_url"], item["evidence_quote"])]
        value = context(citations=citations, proposals=[item], verification=evidence(citations))
    client = service.client_factory.return_value
    if failure == "invalid":
        client.complete.return_value = {"invalid": "untrusted model payload"}
        expected = "invalid_verification_result"
    else:
        client.complete.side_effect = tasks.AnalysisError("codex_rate_limited", retry_after=90)
        client.retry_after = 90
        expected = "codex_rate_limited"
    result = service.evaluate(value, tasks.ModelAttempt(0))
    assert result.reason == expected and result.verification == evidence(citations)
    if failure == "capacity":
        pause.assert_called_once_with(90, reason=expected)
        assert result.retry_after == 600
    else:
        pause.assert_not_called()


@pytest.mark.parametrize("failure", [None, "evaluate", "apply"])
def test_run_propagates_inference_identity_and_records_attempt_even_after_failure(
    service, monkeypatch, failure
):
    value = context()
    service.claim = Mock(return_value=value)
    client, outcome = object(), object()
    seen = []

    def evaluate(context, attempt):
        seen.append(inference_metadata())
        attempt.client = client
        if failure == "evaluate":
            raise RuntimeError("remote detail")
        return outcome

    service.evaluate = evaluate
    service.apply = Mock(side_effect=RuntimeError("apply failed")) if failure == "apply" else Mock()
    service.fail = Mock()
    record = Mock()
    monkeypatch.setattr(tasks, "record_attempt", record)
    service.run(value.identifier)
    assert seen == [
        {
            "operation": "research_verification",
            "job_id": value.identifier,
            "attempt": 1,
            "reason": "verify_research",
        }
    ]
    assert record.call_args.args[:4] == (
        service.factory,
        ResearchVerificationJob,
        value.identifier,
        client,
    )
    assert record.call_args.kwargs == {"attempt": 1}
    if failure:
        assert service.fail.call_args.args[0] is value
        assert isinstance(service.fail.call_args.args[1], RuntimeError)
    else:
        service.apply.assert_called_once_with(value, outcome)
        service.fail.assert_not_called()


def test_run_without_claim_does_not_call_external_services_or_record_attempt(service, monkeypatch):
    service.claim = Mock(return_value=None)
    service.evaluate, service.apply = Mock(), Mock()
    record = Mock()
    monkeypatch.setattr(tasks, "record_attempt", record)
    service.run(uuid.uuid4())
    service.evaluate.assert_not_called()
    service.apply.assert_not_called()
    record.assert_not_called()


@pytest.fixture
def persisted(service, completed, monkeypatch):
    job, proposal = completed
    task = ResearchVerificationJob(
        id=job.id,
        relationships=False,
        status="queued",
        attempts=0,
        usage={},
        created_at=NOW,
        available_at=NOW,
        dispatched_at=NOW,
    )
    token = start_job(task, NOW, 300)
    session = Mock()
    session.get.return_value = proposal
    service.factory.begin.return_value = nullcontext(session)
    owned = Mock(return_value=task)
    monkeypatch.setattr(tasks, "owned_job", owned)
    monkeypatch.setattr(tasks, "_locked", Mock(return_value=job))
    monkeypatch.setattr(tasks, "lock_topics", Mock())
    monkeypatch.setattr(research_verification, "utcnow", lambda: NOW)
    monkeypatch.setattr(analysis, "utcnow", lambda: NOW)
    return SimpleNamespace(
        job=job,
        proposal=proposal,
        task=task,
        session=session,
        owned=owned,
        context=context(
            identifier=job.id,
            token=token,
            citations=[(item["url"], item["quote"]) for item in job.result["sources"]],
            metadata=topic_verification.verification_input(proposal),
        ),
    )


@pytest.mark.parametrize("approved", [False, True])
def test_apply_preserves_research_and_bounds_verification_audit_then_queues_only_approved_metadata(
    service, persisted, monkeypatch, approved
):
    value = persisted
    original = copy.deepcopy(value.job.result)
    current_time = NOW + timedelta(minutes=21)
    service.clock = lambda: current_time
    value.task.available_at = value.task.dispatched_at = current_time
    value.task.lease_until = current_time + timedelta(minutes=5)
    prior_attempts = [
        {"attempt": 1, "checked_at": (NOW + timedelta(minutes=i + 1)).isoformat()}
        for i in range(20)
    ]
    value.job.result = {
        **value.job.result,
        # Earlier policy cycles retain a bounded audit while each new cycle has its own budget.
        "verification_attempts": copy.deepcopy(prior_attempts),
    }
    approve, queue = Mock(), Mock()
    monkeypatch.setattr(tasks, "auto_approve_research", approve)
    monkeypatch.setattr(tasks, "queue_relationships_after_enrichment", queue)
    if approved:
        # A completed editorial decision can arrive while external verification runs.
        value.proposal.status, value.proposal.reviewed_at = "approved", current_time
        value.proposal.reviewed_by = {"subject": "editor"}
        value.proposal.applied = copy.deepcopy(value.proposal.proposed)
    result = tasks.VerificationOutcome(
        evidence(value.context.citations),
        {"version": VERSION, "checks": {}},
        topic_verification.checked_verdict(
            topic_output(value.context.metadata), value.context.metadata
        ),
        None,
        0,
        2,
        0,
        True,
    )
    service.apply(value.context, result)
    for key, expected in original.items():
        assert value.job.result[key] == expected
    assert len(value.job.result["verification_attempts"]) == 20
    assert value.job.result["verification_attempts"][0] == prior_attempts[1]
    assert value.job.result["verification_attempts"][-1] == {
        "attempt": 1,
        "checked_at": current_time.isoformat(),
        "error": None,
        "citations_retried": 2,
        "relationships_checked": 0,
        "topics_checked": 1,
    }
    assert value.task.status == "succeeded"
    assert value.task.outcome == ("approved" if approved else "review_required")
    approve.assert_called_once_with(value.session, value.job)
    if approved:
        queue.assert_called_once_with(value.session, value.proposal, value.job)
    else:
        queue.assert_not_called()


def test_apply_missing_ownership_cannot_rewrite_research_or_review(service, persisted, monkeypatch):
    before = copy.deepcopy(persisted.job.result)
    persisted.owned.return_value = None
    approve = Mock()
    monkeypatch.setattr(tasks, "auto_approve_research", approve)
    service.apply(persisted.context, tasks.VerificationOutcome({}, {}, {}, None, 0, 0, 0, False))
    assert persisted.job.result == before and persisted.proposal.status == "pending"
    approve.assert_not_called()


@pytest.mark.parametrize("failure", ["dependency", "capacity", "lost_lease"])
def test_failure_persists_safe_categories_and_honors_shared_capacity_pause(
    service, persisted, monkeypatch, caplog, failure
):
    pause = Mock(return_value=600)
    monkeypatch.setattr(tasks, "safe_pause", pause)
    if failure == "capacity":
        error = tasks.AnalysisError("codex_server_overloaded", retry_after=120)
    else:
        error = RuntimeError("https://signed.example/?token=secret")
    if failure == "lost_lease":
        persisted.owned.return_value = None
    service.fail(persisted.context, error)
    if failure == "lost_lease":
        assert persisted.task.status == "running" and persisted.task.error is None
    elif failure == "capacity":
        pause.assert_called_once_with(120, reason="codex_server_overloaded")
        assert persisted.task.error == "codex_server_overloaded"
        assert persisted.task.available_at == NOW + timedelta(seconds=600)
    else:
        assert persisted.task.error == "verification_dependency_failure"
        assert persisted.task.available_at == NOW + timedelta(seconds=300)
    assert "token=secret" not in caplog.text

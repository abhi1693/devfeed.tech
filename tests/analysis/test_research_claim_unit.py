"""Verification admission uses current immutable research and retains disabled-policy work."""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import research_verification_tasks as worker
from devfeed_core import article_topic_policy, topic_decision_budget, topic_verification
from devfeed_core.models import ResearchVerificationJob
from devfeed_core.research_verification import POLICY_VERSION

from tests.analysis.test_research_input_guards_unit import NOW as RESEARCH_NOW
from tests.analysis.test_research_input_guards_unit import clock as clock
from tests.analysis.test_research_input_guards_unit import completed as completed
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import relationship_job, relationship_proposal, topic
from tests.taxonomy.test_topic_verification import output as topic_output

NOW = RESEARCH_NOW + timedelta(minutes=1)


def completed_relationship():
    focal, peer = topic(), topic("JavaScript", kind="language")
    parent = relationship_job(focal, peer)
    row = relationship_proposal(focal, peer, parent)
    parent.created_at = RESEARCH_NOW - timedelta(minutes=30)
    parent.available_at = RESEARCH_NOW - timedelta(minutes=29)
    parent.finished_at = RESEARCH_NOW - timedelta(minutes=20)
    row.created_at = RESEARCH_NOW - timedelta(minutes=21)
    return parent


def pending(parent, *, relationships=False):
    return ResearchVerificationJob(
        id=parent.id,
        relationships=relationships,
        status="queued",
        attempts=0,
        created_at=RESEARCH_NOW + timedelta(seconds=10),
        available_at=RESEARCH_NOW + timedelta(seconds=20),
        dispatched_at=RESEARCH_NOW + timedelta(seconds=30),
        lease_token=None,
        lease_until=None,
    )


@pytest.fixture
def runtime(monkeypatch):
    session = Mock()

    @contextmanager
    def begin():
        yield session

    value = SimpleNamespace(
        session=session,
        factory=SimpleNamespace(begin=begin),
        settings=configuration(
            ai_enabled=True,
            auto_approve_topics=True,
            auto_approve_topic_relationships=True,
            article_topic_proposals_enabled=False,
        ),
    )
    value.client, value.citations = Mock(), Mock()
    value.service = worker.ResearchVerificationService(
        value.settings, value.factory, value.client, value.citations, lambda: NOW
    )
    monkeypatch.setattr(worker, "lock_topics", Mock())
    monkeypatch.setattr(article_topic_policy, "get_settings", lambda: value.settings)
    monkeypatch.setattr(topic_decision_budget, "relationship_allowance", Mock(return_value=1))
    return value


@pytest.mark.parametrize("guard", ["absent", "delayed", "running"])
def test_verification_claim_never_steals_active_work_or_ignores_provider_delay(
    runtime, completed, guard
):
    parent, _ = completed
    task = pending(parent)
    if guard == "delayed":
        task.available_at = NOW + timedelta(minutes=5)
    elif guard == "running":
        task.status, task.attempts, task.lease_token, task.lease_until = (
            "running",
            1,
            uuid.uuid4(),
            NOW + timedelta(minutes=4),
        )
    before = copy.deepcopy((task.status, task.attempts, task.lease_token, task.available_at))
    runtime.session.scalar.return_value = None if guard == "absent" else task
    assert runtime.service.claim(parent.id) is None
    assert before == (task.status, task.attempts, task.lease_token, task.available_at)
    runtime.session.get.assert_not_called()
    runtime.client.assert_not_called()


@pytest.mark.parametrize("lane", ["metadata", "relationships"])
@pytest.mark.parametrize("disabled", ["ai", "policy"])
def test_disabled_verification_policy_keeps_the_task_queued_without_spending_an_attempt(
    runtime, completed, lane, disabled
):
    parent, _ = completed
    if lane == "relationships":
        parent = completed_relationship()
    task = pending(parent, relationships=lane == "relationships")
    if disabled == "ai":
        runtime.settings.ai_enabled = False
    else:
        setattr(
            runtime.settings,
            "auto_approve_topic_relationships" if task.relationships else "auto_approve_topics",
            False,
        )
    runtime.session.scalar.return_value = task
    assert runtime.service.claim(parent.id) is None
    assert task.status == "queued" and task.attempts == 0 and task.dispatched_at is None
    runtime.session.get.assert_not_called()
    runtime.client.assert_not_called()


def test_relationship_budget_exhaustion_defers_without_claiming_or_discarding_the_task(
    runtime, completed, monkeypatch
):
    parent, _ = completed
    parent = completed_relationship()
    task = pending(parent, relationships=True)
    runtime.session.scalar.return_value = task
    monkeypatch.setattr(topic_decision_budget, "relationship_allowance", lambda _: 0)
    assert runtime.service.claim(parent.id) is None
    assert task.available_at == NOW + timedelta(minutes=5) and task.dispatched_at is None
    assert task.status == "queued" and task.attempts == 0 and task.lease_token is None


@pytest.mark.parametrize("change", ["current", "edited", "reviewed", "paused-origin"])
def test_metadata_claim_rechecks_current_draft_and_operator_decision(runtime, completed, change):
    parent, proposal = completed
    task = pending(parent)
    if change == "edited":
        proposal.proposed = {
            **proposal.proposed,
            "description": "A corrected operator description.",
        }
    elif change == "reviewed":
        proposal.status, proposal.reviewed_at, proposal.reviewed_by = (
            "rejected",
            NOW,
            {"subject": "editor"},
        )
    elif change == "paused-origin":
        proposal.origin = (
            "article_enrichment"  # Old durable work predating the pause remains queued.
        )
    runtime.session.scalar.side_effect = [task, parent, proposal]
    runtime.session.get.return_value = proposal
    before = copy.deepcopy((proposal.proposed, proposal.status))
    context = runtime.service.claim(parent.id)
    assert before == (proposal.proposed, proposal.status)
    if change == "current":
        assert context.metadata == topic_verification.verification_input(proposal)
        assert context.citations == [
            (item["url"], item["quote"]) for item in parent.result["sources"]
        ]
        assert context.attempt == 1 and context.token == task.lease_token
        assert task.status == "running" and task.lease_until == NOW + timedelta(minutes=5)
        assert parent.result["verification_policy_version"] == POLICY_VERSION
    elif change == "paused-origin":
        assert context is None and task.status == "queued" and task.attempts == 0
        assert task.available_at == NOW + timedelta(minutes=5) and task.dispatched_at is None
    else:
        assert context is None and task.status == "succeeded" and task.outcome == "superseded"
        assert task.attempts == 0 and task.lease_token is None
    runtime.client.assert_not_called()
    runtime.citations.assert_not_called()


@pytest.mark.parametrize("receipt", ["decided", "uncertain", "old-version", "changed-input"])
def test_claim_rechecks_decided_identity_citations_but_allows_uncertain_recovery(
    runtime, completed, receipt
):
    parent, proposal = completed
    metadata = topic_verification.verification_input(proposal)
    output = topic_output(metadata)
    # A negative prior verdict is a coherent completed check; transport may be retried.
    output["verdict"] = "uncertain" if receipt == "uncertain" else "unsupported"
    output["relevance"].update(
        verdict="uncertain" if receipt == "uncertain" else "out_of_scope", sources=[]
    )
    for check in output["fields"]:
        check.update(supported=False, sources=[])
    for check in output["aliases"]:
        check.update(same_identity=False, sources=[])
    semantic = topic_verification.checked_verdict(output, metadata)
    if receipt == "old-version":
        semantic["version"] = "retired-identity-policy"
    elif receipt == "changed-input":
        semantic["check"]["input_hash"] = "f" * 64
    parent.result["topic_verification"] = semantic
    task = pending(parent)
    runtime.session.scalar.side_effect = [task, parent, proposal]
    runtime.session.get.return_value = proposal
    context = runtime.service.claim(parent.id)
    research_quotes = [(item["url"], item["quote"]) for item in parent.result["sources"]]
    assert context.citations == research_quotes + (
        [(item["url"], item["quote"]) for item in semantic["check"]["sources"]]
        if receipt == "decided"
        else []
    )
    assert context.topic_semantic == (
        {} if receipt in {"old-version", "changed-input"} else semantic
    )


@pytest.mark.parametrize("retired", [False, True])
def test_relationship_claim_requires_current_endpoint_identities(runtime, monkeypatch, retired):
    focal, peer = topic(), topic("JavaScript", kind="language")
    # This helper uses Oct23; shift its complete history before the worker clock.
    parent = relationship_job(focal, peer)
    proposal = relationship_proposal(focal, peer, parent)
    parent.created_at, parent.available_at, parent.finished_at = (
        RESEARCH_NOW - timedelta(minutes=30),
        RESEARCH_NOW - timedelta(minutes=29),
        RESEARCH_NOW - timedelta(minutes=20),
    )
    proposal.created_at = RESEARCH_NOW - timedelta(minutes=21)
    if retired:
        peer.status = "rejected"
    task = pending(parent, relationships=True)
    runtime.session.scalar.side_effect = [task, parent]
    runtime.session.scalars.side_effect = [SimpleNamespace(all=lambda: [proposal]), [focal, peer]]
    context = runtime.service.claim(parent.id)
    if retired:
        assert context is None and task.outcome == "superseded" and task.attempts == 0
    else:
        assert context.metadata is None and len(context.proposals) == 1
        assert context.proposals[0]["proposal_id"] == str(proposal.id)
        assert context.citations == [(proposal.evidence_url, proposal.evidence_quote)]
        assert task.status == "running" and task.attempts == 1
    runtime.client.assert_not_called()


def test_public_worker_entrypoint_keeps_job_log_and_service_inputs_bound_to_the_same_uuid(
    monkeypatch,
):
    identifier = uuid.uuid4()
    settings, factory = object(), object()
    constructor = Mock()
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "session_factory", lambda: factory)
    monkeypatch.setattr(worker, "ResearchVerificationService", constructor)
    worker.verify_research(str(identifier))
    constructor.assert_called_once_with(
        settings, factory, worker.CodexClient, worker.verify_citations, worker.utcnow
    )
    constructor.return_value.run.assert_called_once_with(identifier)

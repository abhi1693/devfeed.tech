"""Decision-worker lease ownership, persisted budgets, and unlocked model transport."""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_aggregator import topic_decision_tasks as worker
from devfeed_core.analysis import snapshot_hash
from devfeed_core.config import get_settings
from devfeed_core.models import TopicAnalysisJob, TopicDecisionRun, TopicProposal
from devfeed_core.topic_decision_budget import initial_state
from devfeed_core.topic_decisions import DecisionDeferred

NOW = datetime(2026, 10, 4, tzinfo=UTC)
ID, PROPOSAL, TOKEN = (uuid.UUID(int=i) for i in (1, 2, 3))
TOPIC = {"name": "Python", "slug": "python"}


@pytest.fixture
def harness(monkeypatch):
    settings = get_settings().model_copy(
        update={"codex_model": "unit-model", "full_automation": False}
    )
    proposal = TopicProposal(id=PROPOSAL, proposed=dict(TOPIC), status="pending", evidence=[])
    job = TopicAnalysisJob(
        id=ID,
        proposal_id=PROPOSAL,
        input_hash=snapshot_hash(TOPIC),
        status="running",
        attempts=2,
        lease_token=TOKEN,
        result={},
        usage={},
        duration_ms=0,
    )
    initial_run = TopicDecisionRun(
        proposal_id=PROPOSAL,
        input_hash=job.input_hash,
        status="active",
        state=initial_state(settings),
    )
    state = NS(
        run=initial_run,
        proposal=proposal,
        owned=True,
        in_transaction=0,
        phase="start",
        complete_error=None,
        settled_elsewhere=False,
    )
    session, factory = Mock(), Mock()

    @contextmanager
    def transaction():
        state.in_transaction += 1
        try:
            yield session
        finally:
            state.in_transaction -= 1

    factory.begin.side_effect = transaction
    session.get.side_effect = lambda model, *_args, **_kwargs: (
        state.proposal if model is TopicProposal else state.run
    )
    session.scalar.return_value = job

    def added(value):
        if isinstance(value, TopicDecisionRun):
            state.run = value

    session.add.side_effect = added
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    monkeypatch.setattr(worker, "owned_job", lambda *_: job if state.owned else None)
    monkeypatch.setattr(worker, "lock_topics", Mock())
    clients = []

    def client(config):
        result = Mock(usage={"inputTokens": 100, "outputTokens": 30}, web_search_count=1)
        result.config = config

        def complete(*_args, **_kwargs):
            assert state.in_transaction == 0, "Model transport must release database locks"
            if state.settled_elsewhere:
                ledger = state.run.state
                ledger["calls"][-1].update(status="returned", seconds=0.01)
            if state.complete_error:
                raise state.complete_error
            return {"outcome": "ready"}

        result.complete.side_effect = complete
        clients.append(result)
        return result

    monkeypatch.setattr(worker, "CodexClient", client)
    fetch = Mock(return_value={"pages": [], "failures": []})
    monkeypatch.setattr(worker, "fetch_bundle", fetch)

    def approve(_, __):
        proposal.status = "approved"

    approve = Mock(side_effect=approve)
    monkeypatch.setattr(worker, "auto_approve_research", approve)
    review = Mock(side_effect=lambda *_: setattr(proposal, "status", "rejected"))
    monkeypatch.setattr(worker, "review_proposal", review)
    pause = Mock(return_value=60)
    monkeypatch.setattr(worker, "safe_pause", pause)

    def execute(decision):
        monkeypatch.setattr(worker, "run_decision", decision)
        worker.execute_decision(factory, ID, TOKEN, PROPOSAL, TOPIC)

    return NS(
        settings=settings,
        proposal=proposal,
        job=job,
        state=state,
        session=session,
        clients=clients,
        fetch=fetch,
        approve=approve,
        review=review,
        pause=pause,
        execute=execute,
    )


def exercise_callbacks(_id, topic, state, *, call, save, fetch):
    assert fetch(["https://example.com/"]) == {"pages": [], "failures": []}
    save("discovery", {"sources": []})
    assert call("discovery", "Discover exact identity", {}, web=True) == {"outcome": "ready"}
    call("draft", "Draft from source evidence", {}, escalated=True)
    call("verification", "Independently verify", {})
    return {
        "decision": "approved",
        "topic": {**topic, "description": "Developer tool"},
        "sources": [],
    }


@pytest.mark.parametrize("new_run", [False, True])
def test_worker_persists_each_call_budget_and_charges_without_holding_locks(harness, new_run):
    if new_run:
        harness.state.run = None
    harness.execute(exercise_callbacks)
    run, job = harness.state.run, harness.job
    assert [call["status"] for call in run.state["calls"]] == ["returned"] * 3
    assert [client.operation for client in harness.clients] == [
        "topic_discovery",
        "topic_draft",
        "topic_verification",
    ]
    assert harness.clients[0].search_limit > 0 and harness.clients[1].search_limit == 0
    assert harness.clients[1].reason == "validation_escalation"
    assert all(
        client.token_limit > 0 and client.config.codex_timeout_seconds >= 10
        for client in harness.clients
    )
    assert (
        job.usage["inputTokens"] == 300
        and job.usage["outputTokens"] == 90
        and job.usage["totalTokens"] == 390
    )
    assert job.status == "succeeded" and job.outcome == "enriched"
    assert job.lease_token is job.lease_until is None
    assert run.status == "decided" and run.reason is None
    assert "evidence_seconds" in run.state and "discovery" in run.state
    assert harness.proposal.evidence[0]["before"] == TOPIC
    assert harness.proposal.evidence[0]["analysis_id"] == str(ID)
    assert job.result["applied_input_hash"] == snapshot_hash(harness.proposal.proposed)


def test_settlement_is_idempotent_when_another_transaction_already_charged_the_call(harness):
    harness.state.settled_elsewhere = True
    harness.execute(exercise_callbacks)
    assert harness.job.usage == {} and harness.job.duration_ms == 0
    assert all(call["status"] == "returned" for call in harness.state.run.state["calls"])


@pytest.mark.parametrize("full", [False, True])
def test_out_of_scope_result_only_reviews_the_proposal_in_full_automation(harness, full):
    harness.settings.full_automation = full
    harness.execute(
        lambda *_args, **_kwargs: {"decision": "rejected", "topic": TOPIC, "sources": []}
    )
    assert harness.job.outcome == "out_of_scope"
    assert harness.review.call_count == int(full) and harness.approve.call_count == 0
    assert harness.state.run.status == ("decided" if full else "review")
    assert harness.state.run.reason == (None if full else "manual_review_required")


def test_unapproved_enrichment_remains_available_for_manual_review(harness):
    harness.approve.side_effect = None
    harness.execute(exercise_callbacks)
    assert harness.state.run.status == "review" and harness.proposal.status == "pending"
    assert harness.state.run.reason == "manual_review_required"


@pytest.mark.parametrize(
    "change", ["missing", "reviewed", "hash", "run-hash", "inactive", "inactive-reason"]
)
def test_invalid_initial_state_defers_without_spending_model_budget(harness, change):
    if change == "missing":
        harness.state.proposal = None
    elif change == "reviewed":
        harness.proposal.status = "approved"
    elif change == "hash":
        harness.proposal.proposed = {"name": "Changed"}
    elif change == "run-hash":
        harness.state.run.input_hash = "old"
    else:
        harness.state.run.status = "deferred"
        harness.state.run.reason = "manual_hold" if change == "inactive-reason" else None
    harness.execute(exercise_callbacks)
    assert harness.job.outcome == "decision_deferred"
    assert harness.job.result["deferred_reason"] == (
        "manual_hold" if change == "inactive-reason" else "proposal_changed"
    )
    assert harness.clients == []


@pytest.mark.parametrize("phase", ["initial", "final", "error"])
def test_lost_lease_never_applies_or_overwrites_a_job(harness, phase):
    def decision(*_args, **_kwargs):
        harness.state.owned = False
        if phase == "error":
            raise RuntimeError("dependency")
        return {"decision": "approved", "topic": TOPIC, "sources": []}

    if phase == "initial":
        harness.state.owned = False
    harness.execute(decision)
    assert harness.job.status == "running" and harness.job.result == {}
    assert harness.proposal.proposed == TOPIC and harness.proposal.evidence == []


@pytest.mark.parametrize("change", ["reviewed", "changed"])
def test_proposal_changed_during_transport_is_superseded(harness, change):
    def decision(*_args, **_kwargs):
        if change == "reviewed":
            harness.proposal.status = "approved"
        else:
            harness.proposal.proposed = {"name": "Other"}
        return {"decision": "approved", "topic": TOPIC, "sources": []}

    harness.execute(decision)
    assert harness.job.outcome == "superseded"
    assert harness.state.run.status == "deferred" and harness.state.run.reason == "proposal_changed"
    harness.approve.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        DecisionDeferred("token_budget_exhausted"),
        worker.AnalysisError("codex_server_overloaded"),
        RuntimeError("private dependency detail"),
    ],
)
def test_failed_calls_keep_charges_and_dependency_details_are_not_exposed(harness, error):
    harness.state.complete_error = error
    harness.execute(exercise_callbacks)
    run, job = harness.state.run, harness.job
    assert len(run.state["calls"]) == 1 and run.state["calls"][0]["status"] == "failed"
    assert run.state["calls"][0]["charged_tokens"] == run.state["calls"][0]["reserved_tokens"]
    assert job.usage["totalTokens"] == 130
    if str(error) == "codex_server_overloaded":
        assert job.status == "queued" and run.status == "active"
        harness.pause.assert_called_once()
    else:
        assert job.outcome == "decision_deferred" and run.status == "deferred"
        assert job.result["deferred_reason"] == (
            str(error) if isinstance(error, DecisionDeferred) else "decision_dependency_failure"
        )
        assert "private dependency detail" not in str(job.result)


@pytest.mark.parametrize("reason", [None, "operator_hold"])
def test_run_paused_between_calls_cannot_reserve_additional_budget(harness, reason):
    def decision(_id, _topic, _state, *, call, **_kwargs):
        harness.state.run.status, harness.state.run.reason = "review", reason
        call("discovery", "prompt", {})

    harness.execute(decision)
    assert harness.clients == [] and harness.state.run.state["calls"] == []
    assert harness.job.result["deferred_reason"] == (reason or "decision_not_active")

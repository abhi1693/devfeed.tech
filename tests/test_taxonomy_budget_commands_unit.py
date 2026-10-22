"""Operator grants and scheduling respect durable spent budgets and admission results."""

import copy
import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import db, quota_pacing, topic_decisions
from devfeed_core import topic_decision_budget as budget
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import InferenceCall, TopicAnalysisJob, TopicDecisionRun, TopicProposal
from devfeed_core.services import OperationConflict
from taxonomy_command_helpers import ACTOR, NOW, proposal
from test_source_readiness_unit import configuration


@pytest.fixture
def runtime(monkeypatch):
    old = configuration(topic_decision_call_tokens=4000)
    value = SimpleNamespace(
        now=NOW - timedelta(minutes=30), settings=old, proposal=proposal(), session=MagicMock()
    )
    monkeypatch.setattr(budget, "get_settings", lambda: value.settings)
    monkeypatch.setattr(budget, "utcnow", lambda: value.now)
    state = budget.reserve(budget.initial_state(old), "discovery", web=True)
    value.now += timedelta(seconds=5)
    state = budget.settle(
        state,
        0,
        usage={"input_tokens": 60, "output_tokens": 40},
        searches=1,
        seconds=5,
        model="test-model",
        effort="low",
        succeeded=True,
    )
    state["discovery"] = topic_decisions.DiscoveryResult(
        sources=[{"url": "https://react.dev/learn"}], reason="Primary developer documentation."
    ).model_dump(mode="json")
    text = "React components are JavaScript functions that return markup."
    page = topic_decisions.page_snapshot(
        "https://react.dev/learn",
        {
            "text": text,
            "final_url": "https://react.dev/learn",
            "content_hash": hashlib.sha256(f"<p>{text}</p>".encode()).hexdigest(),
            "validated_at": value.now.isoformat(),
        },
    )
    state["evidence"] = {"version": topic_decisions.VERSION, "pages": [page], "failures": []}
    value.now += timedelta(seconds=15)
    state = budget.reserve(state, "draft")
    value.run = TopicDecisionRun(
        proposal_id=value.proposal.id,
        input_hash=snapshot_hash(value.proposal.proposed),
        status="review",
        reason="interrupted_call_requires_review",
        state=state,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=20),
    )
    value.jobs = [
        TopicAnalysisJob(
            id=uuid.uuid4(),
            proposal_id=value.proposal.id,
            input_snapshot={"topic": copy.deepcopy(value.proposal.proposed), "evidence": []},
            input_hash=value.run.input_hash,
            prompt_version=budget.VERSION,
            requested_by=ACTOR,
            status="failed",
            attempts=1,
            error="worker_interrupted",
            result={},
            usage={},
            created_at=NOW - timedelta(minutes=40),
            available_at=NOW - timedelta(minutes=39),
            finished_at=NOW - timedelta(minutes=20),
        )
    ]
    value.now, value.settings = NOW, configuration(topic_decision_call_tokens=8000)
    value.session.scalars.return_value.all.side_effect = lambda: value.jobs
    value.session.get.side_effect = lambda model, _id, **_: (
        value.proposal if model is TopicProposal else value.run
    )
    value.inserted = []

    def add(row):
        row.id = uuid.uuid4()
        value.inserted.append(row)

    value.session.add.side_effect = add
    return value


def grant(value, **changes):
    return budget.grant_budget(
        value.session,
        value.proposal.id,
        **{
            "calls": 2,
            "tokens": 16000,
            "note": "Reviewed the interrupted reservation; allow a bounded retry.",
            "actor": ACTOR["subject"],
            **changes,
        },
    )


def test_explicit_grant_preserves_spent_reservations_and_records_the_previous_state(runtime):
    before = copy.deepcopy(runtime.run.state)
    previous_hash = runtime.run.input_hash
    runtime.proposal.proposed = {**runtime.proposal.proposed, "keywords": ["human-corrected"]}
    result = grant(runtime)
    state = runtime.run.state
    assert runtime.run.status == "active" and runtime.run.reason is None
    assert runtime.run.input_hash == snapshot_hash(runtime.proposal.proposed)
    assert runtime.run.updated_at == NOW
    assert state["calls"][0] == before["calls"][0]
    assert state["calls"][1] == {**before["calls"][1], "status": "unknown"}
    assert state["calls"][1]["charged_tokens"] == 4000
    assert state["limits"] == {
        **{
            key: before["limits"][key] + extra
            for key, extra in {
                "calls": 2,
                "tokens": 16000,
                "searches": 4,
                "seconds": 240,
                "escalations": 1,
            }.items()
        },
        "call_tokens": 8000,
    }
    audit = state["grants"][0]
    assert audit["previous_input_hash"] == previous_hash
    assert audit["previous_state_hash"] == snapshot_hash(before)
    assert audit["previous_call_tokens"] == 4000 and audit["call_tokens"] == 8000
    assert audit["actor"] == ACTOR["subject"] and audit["granted_at"] == NOW.isoformat()
    assert not set(state).intersection({"discovery", "evidence"})
    job = runtime.inserted[0]
    assert result["job_id"] == str(job.id) and result["limits"] == state["limits"]
    assert job.proposal_id == runtime.proposal.id and job.input_hash == runtime.run.input_hash
    assert job.input_snapshot == {"topic": runtime.proposal.proposed, "evidence": []}
    assert job.requested_by["subject"] == ACTOR["subject"] and job.prompt_version == budget.VERSION
    assert runtime.proposal.status == "pending"


@pytest.mark.parametrize(
    "values",
    [
        {"calls": 0},
        {"calls": 6},
        {"tokens": 1999},
        {"tokens": 64001},
        {"note": " "},
        {"note": "x" * 501},
        {"actor": " "},
        {"actor": "x" * 101},
    ],
)
def test_invalid_grant_is_refused_before_reading_or_mutating_budget(runtime, values):
    before = copy.deepcopy(runtime.run.state)
    with pytest.raises(ValueError, match="1-5 calls"):
        grant(runtime, **values)
    runtime.session.scalars.assert_not_called()
    runtime.session.add.assert_not_called()
    assert runtime.run.state == before


@pytest.mark.parametrize(
    "guard",
    [
        "missing-proposal",
        "reviewed-proposal",
        "missing-run",
        "active-run",
        "queued-job",
        "running-job",
    ],
)
def test_grants_require_an_eligible_manual_case_without_current_work(runtime, guard):
    if guard == "missing-proposal":
        runtime.proposal = None
    elif guard == "reviewed-proposal":
        runtime.proposal.status, runtime.proposal.reviewed_at, runtime.proposal.reviewed_by = (
            "rejected",
            NOW,
            ACTOR,
        )
    elif guard == "missing-run":
        runtime.run = None
    elif guard == "active-run":
        runtime.run.status = "active"
    else:
        runtime.jobs = [
            TopicAnalysisJob(
                id=uuid.uuid4(),
                proposal_id=runtime.proposal.id,
                input_snapshot={"topic": runtime.proposal.proposed, "evidence": []},
                input_hash=runtime.run.input_hash,
                requested_by=ACTOR,
                prompt_version=budget.VERSION,
                status="queued" if guard == "queued-job" else "running",
                attempts=0 if guard == "queued-job" else 1,
                created_at=NOW - timedelta(minutes=1),
                available_at=NOW - timedelta(minutes=1),
                lease_token=uuid.uuid4() if guard == "running-job" else None,
                lease_until=NOW + timedelta(minutes=5) if guard == "running-job" else None,
            )
        ]
    identifier = runtime.run.proposal_id if runtime.run else runtime.proposal.id
    with pytest.raises(OperationConflict):
        budget.grant_budget(
            runtime.session,
            identifier,
            calls=1,
            tokens=2000,
            note="Explicit operator retry",
            actor=ACTOR["subject"],
        )
    runtime.session.add.assert_not_called()


def test_two_grants_cannot_be_used_to_reset_budget_a_third_time(runtime):
    for index in range(2):
        runtime.now = NOW + timedelta(minutes=5 * index)
        grant(runtime)
        queued = runtime.inserted[-1]
        # This read models that dispatched attempt failing before the next operator action.
        loaded = TopicAnalysisJob(
            id=queued.id,
            proposal_id=queued.proposal_id,
            input_snapshot=queued.input_snapshot,
            input_hash=queued.input_hash,
            requested_by=queued.requested_by,
            prompt_version=queued.prompt_version,
            status="failed",
            attempts=1,
            error="codex_timeout",
            result={},
            usage={},
            created_at=runtime.now,
            available_at=runtime.now,
            finished_at=runtime.now + timedelta(minutes=1),
        )
        runtime.jobs.append(loaded)
        runtime.run.state = budget.reserve(runtime.run.state, "discover")
        runtime.now += timedelta(seconds=20)
        runtime.run.state = budget.settle(
            runtime.run.state,
            len(runtime.run.state["calls"]) - 1,
            usage={},
            searches=0,
            seconds=20,
            model="test-model",
            effort="low",
            succeeded=False,
        )
        runtime.run.status, runtime.run.reason = "deferred", "analysis_attempts_exhausted"
        runtime.now = loaded.finished_at
        runtime.run.updated_at = loaded.finished_at
    runtime.now = NOW + timedelta(minutes=10)
    before = copy.deepcopy(runtime.run.state)
    with pytest.raises(OperationConflict, match="Two additional budgets"):
        grant(runtime)
    assert runtime.run.state == before and len(runtime.inserted) == 2


@pytest.mark.parametrize("disabled", ["ai_enabled", "full_automation", "ai_bounded_topics_enabled"])
def test_decision_scheduler_does_not_touch_storage_when_its_authority_is_disabled(
    monkeypatch, disabled
):
    flags = {
        "ai_enabled": True,
        "full_automation": True,
        "ai_bounded_topics_enabled": True,
        disabled: False,
    }
    if disabled == "ai_enabled":
        # Full automation enables AI; the actual off state disables both authorities.
        flags["full_automation"] = False
    monkeypatch.setattr(budget, "get_settings", lambda: configuration(**flags))
    factory = Mock()
    assert budget.schedule_decisions(factory) == 0
    factory.begin.assert_not_called()


@pytest.mark.parametrize("limit,pending,locked", [(0, 0, True), (4, 0, False), (4, 4, True)])
def test_admission_does_not_exceed_capacity_or_a_denied_scheduler_reservation(
    monkeypatch, limit, pending, locked
):
    monkeypatch.setattr(
        budget,
        "get_settings",
        lambda: configuration(
            ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=True
        ),
    )
    monkeypatch.setattr(budget, "topic_admission_limit", lambda _: limit)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalar.side_effect = [locked, pending]
    assert budget.schedule_decisions(factory, capacity=object()) == 0
    session.scalars.assert_not_called()
    session.add.assert_not_called()


def test_admission_combines_reserved_oldest_work_and_demand_without_conferring_approval(
    monkeypatch,
):
    settings = configuration(ai_enabled=True, full_automation=True, ai_bounded_topics_enabled=True)
    monkeypatch.setattr(budget, "get_settings", lambda: settings)
    monkeypatch.setattr(budget, "topic_admission_limit", lambda _: 4)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalar.side_effect = [True, 0]
    fifo = proposal(created_at=NOW - timedelta(hours=4), research_requested=True)
    demanded = [
        proposal(
            proposed={**proposal().proposed, "name": name, "slug": name.lower()},
            slug=name.lower(),
            created_at=NOW - timedelta(hours=1),
            research_requested=True,
        )
        for name in ["Python", "JavaScript", "PostgreSQL"]
    ]
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [fifo]),
        SimpleNamespace(all=lambda: demanded),
    ]
    assert budget.schedule_decisions(factory, capacity=object()) == 4
    jobs = [call.args[0] for call in session.add.call_args_list]
    assert [row.proposal_id for row in jobs] == [fifo.id, *(row.id for row in demanded)]
    for job, current in zip(jobs, [fifo, *demanded], strict=True):
        assert job.input_hash == snapshot_hash(current.proposed)
        assert job.input_snapshot == {"topic": current.proposed, "evidence": []}
        assert job.requested_by["subject"] == budget.VERSION
        assert current.status == "pending" and not current.research_requested


@pytest.mark.parametrize(
    "backlog,calls,allowed", [(True, 0, False), (False, 1, True), (False, 2, False)]
)
def test_relationship_budget_reserves_for_the_topic_backlog_and_counts_running_calls(
    monkeypatch, backlog, calls, allowed
):
    monkeypatch.setattr(
        budget,
        "get_settings",
        lambda: configuration(
            ai_bounded_topics_enabled=True,
            ai_quota_pacing_enabled=False,
            relationship_pause_for_topic_backlog=True,
            relationship_daily_call_budget=2,
        ),
    )
    monkeypatch.setattr(budget, "actionable_topic_backlog", lambda _: backlog)
    session = Mock()
    session.scalar.return_value = calls
    assert budget.relationship_allowance(session) is allowed
    if backlog:
        session.scalar.assert_not_called()


@pytest.mark.parametrize("paused", [False, True])
def test_quota_pacing_authority_controls_relationship_admission_without_reading_legacy_daily_counts(
    monkeypatch, paused
):
    monkeypatch.setattr(budget, "get_settings", lambda: configuration(ai_quota_pacing_enabled=True))
    monkeypatch.setattr(
        quota_pacing, "current_pause_reason", lambda _: "provider_reset" if paused else None
    )
    session = Mock()
    assert budget.relationship_allowance(session) is not paused
    session.scalar.assert_not_called()


@pytest.mark.parametrize("allowed", [False, True])
def test_only_admitted_relationship_calls_create_an_inference_reservation(monkeypatch, allowed):
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    monkeypatch.setattr(db, "session_factory", lambda: factory)
    monkeypatch.setattr(budget, "relationship_allowance", lambda _: allowed)
    values = {
        "id": uuid.uuid4(),
        "operation": "relationship_research",
        "started_at": NOW,
        "job_id": uuid.uuid4(),
        "attempt": 1,
        "model": "test-model",
        "reasoning_effort": "low",
        "request_hash": snapshot_hash({"topic": "React", "peer": "JavaScript"}),
        "status": "running",
        "tokens": {},
        "web_searches": 0,
        "duration_ms": 0,
    }
    assert budget.admit_relationship_call(values) is allowed
    if allowed:
        row = session.add.call_args.args[0]
        assert isinstance(row, InferenceCall) and row.id == values["id"]
        assert row.operation == values["operation"] and row.started_at == NOW
    else:
        session.add.assert_not_called()

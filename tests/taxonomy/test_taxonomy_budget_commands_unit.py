"""Operator grants and scheduling respect durable spent budgets and admission results."""

import asyncio
import copy
import hashlib
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from devfeed_core import quota_pacing, topic_decisions
from devfeed_core import topic_decision_budget as budget
from devfeed_core.analysis import snapshot_hash
from devfeed_core.models import TopicAnalysisJob, TopicDecisionRun, TopicProposal
from devfeed_core.services import OperationConflict

from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import ACTOR, NOW, proposal


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
    "backlog,budget_size,allowed", [(True, 2, False), (False, 2, True), (False, 0, False)]
)
def test_relationship_preflight_checks_policy_without_querying_postgresql_analytics(
    monkeypatch, backlog, budget_size, allowed
):
    monkeypatch.setattr(
        budget,
        "get_settings",
        lambda: configuration(
            ai_bounded_topics_enabled=True,
            ai_quota_pacing_enabled=False,
            relationship_pause_for_topic_backlog=True,
            relationship_daily_call_budget=budget_size,
        ),
    )
    monkeypatch.setattr(budget, "actionable_topic_backlog", lambda _: backlog)
    session = Mock()
    assert budget.relationship_allowance(session) is allowed
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
def test_only_admitted_relationship_calls_reserve_directly_in_clickhouse(monkeypatch, allowed):
    from devfeed_core import analytics_inference_store

    async def run():
        monkeypatch.setattr(budget, "_relationship_policy_allowed", lambda: allowed)
        admit = AsyncMock(return_value=True)
        monkeypatch.setattr(analytics_inference_store.RelationshipQuota, "admit", admit)
        client = AsyncMock()
        client.query.return_value = [{"complete": 1}]
        values = {
            "id": uuid.uuid4(),
            "operation": "relationship_research",
            "started_at": NOW,
            "model": "test-model",
            "reasoning_effort": "low",
            "status": "running",
            "tokens": {},
            "web_searches": 0,
            "duration_ms": 0,
        }
        settings = configuration(ai_quota_pacing_enabled=False, relationship_daily_call_budget=2)
        assert (
            await budget.admit_relationship_call(values, settings=settings, client=client)
            is allowed
        )
        if allowed:
            admit.assert_awaited_once_with(values, 2)
        else:
            admit.assert_not_awaited()
            client.query.assert_not_awaited()

    asyncio.run(run())


def test_relationship_admission_fails_closed_while_historical_import_is_incomplete(monkeypatch):
    async def run():
        monkeypatch.setattr(budget, "_relationship_policy_allowed", lambda: True)
        client = AsyncMock()
        client.query.return_value = []
        settings = configuration(ai_quota_pacing_enabled=False, relationship_daily_call_budget=2)
        assert not await budget.admit_relationship_call({}, settings=settings, client=client)

    asyncio.run(run())


def test_unbounded_topic_policy_needs_no_postgres_analytical_lookup(monkeypatch):
    monkeypatch.setattr(
        budget,
        "get_settings",
        lambda: configuration(ai_bounded_topics_enabled=False, ai_quota_pacing_enabled=False),
    )
    session = Mock()
    assert budget.relationship_allowance(session)
    session.scalar.assert_not_called()


def test_relationship_policy_source_session_closes_after_preflight(monkeypatch):
    from contextlib import contextmanager

    from devfeed_core import db

    events = []
    session = object()

    @contextmanager
    def transaction():
        events.append("open")
        try:
            yield session
        finally:
            events.append("close")

    monkeypatch.setattr(db, "session_factory", lambda: SimpleNamespace(begin=transaction))
    monkeypatch.setattr(budget, "relationship_allowance", lambda current: current is session)
    assert budget._relationship_policy_allowed()
    assert events == ["open", "close"]


def test_relationship_admission_confirms_clickhouse_before_releasing_its_async_lock(monkeypatch):
    from contextlib import asynccontextmanager

    import psycopg

    async def run():
        events = []

        class Connection:
            async def __aenter__(self):
                events.append("connect")
                return self

            async def __aexit__(self, *_args):
                events.append("close")

            @asynccontextmanager
            async def transaction(self):
                events.append("transaction")
                yield
                events.append("commit")

            async def execute(self, statement, parameters):
                events.append("lock" if "advisory" in statement else "deadline")

        async def connect(dsn, *, autocommit):
            assert dsn == "postgresql://fixture@db.invalid/test" and autocommit
            return Connection()

        monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
        monkeypatch.setattr(budget, "_relationship_policy_allowed", lambda: True)
        client = AsyncMock()
        client.query.side_effect = [[{"complete": 1}], [{"calls": 0}], [{"calls": 0}]]

        async def write(table, rows):
            assert table == "analytics_inference_events" and len(rows) == 1
            assert rows[0]["status"] == "running"
            events.append("acknowledged")

        client.insert.side_effect = write
        settings = configuration(
            ai_quota_pacing_enabled=False,
            relationship_daily_call_budget=2,
            database_url="postgresql+psycopg://fixture@db.invalid/test",
        )
        values = dict(
            id=uuid.uuid4(),
            started_at=NOW,
            status="running",
            operation="relationship_research",
            tokens={},
        )
        assert await budget.admit_relationship_call(values, settings=settings, client=client)
        assert events == [
            "connect",
            "transaction",
            "deadline",
            "lock",
            "acknowledged",
            "commit",
            "close",
        ]

    asyncio.run(run())


@pytest.mark.parametrize("error", [TimeoutError(), KeyError("complete"), TypeError(), ValueError()])
def test_ambiguous_quota_admission_never_authorizes_a_paid_call(monkeypatch, error):
    async def run():
        monkeypatch.setattr(budget, "_relationship_policy_allowed", lambda: True)
        client = AsyncMock()
        client.query.side_effect = error
        settings = configuration(ai_quota_pacing_enabled=False)
        assert not await budget.admit_relationship_call({}, settings=settings, client=client)
        client.insert.assert_not_called()

    asyncio.run(run())


def test_provider_pacing_reservation_requires_confirmed_direct_write(monkeypatch):
    from devfeed_core.analytics import AnalyticsUnavailable

    async def run():
        monkeypatch.setattr(budget, "_relationship_policy_allowed", lambda: True)
        client = AsyncMock()
        settings = configuration(ai_quota_pacing_enabled=True)
        values = dict(
            id=uuid.uuid4(),
            started_at=NOW,
            status="running",
            operation="relationship_verification",
            tokens={},
        )
        assert await budget.admit_relationship_call(values, settings=settings, client=client)
        client.insert.assert_awaited_once()
        client.query.assert_not_awaited()
        client.insert.side_effect = AnalyticsUnavailable("offline")
        assert not await budget.admit_relationship_call(values, settings=settings, client=client)

    asyncio.run(run())

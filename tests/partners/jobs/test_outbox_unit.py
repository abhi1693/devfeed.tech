"""Bounded partner publication, backpressure and durable delivery semantics."""

import logging
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import Mock, PropertyMock

import pytest
from devfeed_aggregator.partner_outbox import PartnerOutbox
from devfeed_core.models import PartnerEvaluation, PartnerPipelineJob, utcnow
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def publisher(*, jobs=(), recovery=(), count=0, locked=True, pending=True, batch_size=100):
    session = Mock()
    session.scalar.side_effect = [pending, locked]

    def claims(statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        limit = compiled.params["param_1"]
        candidates = recovery if "dispatched_at IS NOT NULL" in str(compiled) else jobs
        return NS(all=lambda: list(candidates)[:limit])

    session.scalars.side_effect = claims
    queue = Mock(count=count)
    queue.name = "default"
    lookup = Mock(return_value=queue)
    transaction = NS(committed=False, rolled_back=False)

    @contextmanager
    def begin():
        try:
            yield session
        except Exception:
            transaction.rolled_back = True
            raise
        else:
            transaction.committed = True

    factory = Mock()
    factory.begin.side_effect = begin
    outbox = PartnerOutbox(factory, lookup, batch_size=batch_size, now=utcnow())
    return NS(
        outbox=outbox,
        session=session,
        queue=queue,
        lookup=lookup,
        transaction=transaction,
    )


def publish(state, *, queue_name="ingestion", limit=None, model=PartnerPipelineJob):
    return state.outbox.publish(
        queue_name,
        select(model).order_by(model.created_at),
        model=model,
        handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
        delivery_id=lambda job: f"partner-pipeline-{queue_name}-{job.id}",
        event="partner_pipeline_dispatched",
        fields=lambda job: {"job_id": str(job.id)},
        limit=limit,
    )


def job():
    return NS(id=uuid.uuid4(), dispatched_at=None)


@pytest.mark.parametrize("queue_name", ["ingestion", "source-analysis"])
@pytest.mark.parametrize("destination", ["default", "catalog-pipeline"])
def test_physical_queue_lock_covers_backpressure_and_publication(queue_name, destination):
    first, second = job(), job()
    state = publisher(jobs=[first, second])
    state.queue.name = destination
    events = []

    def scalar(statement):
        events.append("lock" if "pg_try_advisory_xact_lock" in str(statement) else "pending")
        return True

    state.session.scalar.side_effect = scalar

    def lookup(name):
        assert events == ["pending"]
        events.append("queue")
        return state.queue

    state.lookup.side_effect = lookup

    def enqueue(*args, **kwargs):
        events.append("enqueue")
        current = first if args[1] == str(first.id) else second
        assert current.dispatched_at is None
        assert not state.transaction.committed

    state.queue.enqueue.side_effect = enqueue
    assert publish(state, queue_name=queue_name) == 2
    state.lookup.assert_called_once_with("partner-pipeline")
    assert state.queue.enqueue.call_args.kwargs["meta"] == {
        "devfeed_ai": queue_name == "source-analysis"
    }
    assert events == ["pending", "queue", "lock", "enqueue", "enqueue"]
    assert state.session.scalar.call_args.args[0].compile().params == {
        "hashtextextended_1": "devfeed:partner-outbox:" + destination,
        "hashtextextended_2": 0,
    }
    assert first.dispatched_at == second.dispatched_at == state.outbox.now
    assert state.transaction.committed
    state.queue.connection.close.assert_called_once()
    for item in state.queue.enqueue.call_args_list:
        assert item.args[0] == "devfeed_aggregator.partner_sync_tasks.process_pipeline"
        assert item.kwargs == {
            "job_id": f"partner-pipeline-{queue_name}-{item.args[1]}",
            "unique": True,
            "job_timeout": 300,
            "result_ttl": 0,
            "failure_ttl": 60,
            "meta": {"devfeed_ai": queue_name == "source-analysis"},
        }


def test_empty_lane_uses_one_bounded_probe_without_redis_or_advisory_lock():
    state = publisher(pending=False)
    assert publish(state) == 0
    state.session.scalar.assert_called_once()
    statement = state.session.scalar.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "EXISTS" in compiled and "ORDER BY" not in compiled
    assert "pg_try_advisory" not in compiled
    state.lookup.assert_not_called()
    state.session.scalars.assert_not_called()
    assert state.transaction.committed


def test_busy_physical_queue_lock_closes_lookup_without_reading_or_publishing():
    state = publisher(locked=False)
    assert publish(state) == 0
    state.lookup.assert_called_once_with("partner-pipeline")
    state.session.scalars.assert_not_called()
    state.queue.enqueue.assert_not_called()
    state.queue.connection.close.assert_called_once()


@pytest.mark.parametrize("count", [200, 201, 1000])
def test_full_shared_queue_leaves_durable_work_unclaimed(count):
    state = publisher(count=count)
    assert publish(state) == 0
    state.session.scalars.assert_not_called()
    state.queue.enqueue.assert_not_called()
    state.queue.connection.close.assert_called_once()


@pytest.mark.parametrize(
    "batch_size,count,limit,expected",
    [(100, 0, None, 100), (100, 150, None, 50), (100, 0, 7, 7), (100, 190, 50, 10), (5, 0, 9, 9)],
)
def test_claim_is_bounded_by_requested_batch_and_queue_headroom(batch_size, count, limit, expected):
    state = publisher(batch_size=batch_size, count=count)
    assert publish(state, limit=limit) == 0
    assert state.session.scalars.call_count == 2
    for claim in state.session.scalars.call_args_list:
        compiled = claim.args[0].compile(dialect=postgresql.dialect())
        assert compiled.params == {"param_1": expected}
        assert "ORDER BY partner_pipeline_jobs.created_at" in str(compiled)
        assert "FOR UPDATE SKIP LOCKED" in str(compiled)


@pytest.mark.parametrize("fresh_count,recovery_count", [(1000, 1000), (1000, 25), (75, 1000)])
def test_fresh_and_recovery_backlogs_both_make_progress(fresh_count, recovery_count):
    fresh = [job() for _ in range(fresh_count)]
    recovery = [job() for _ in range(recovery_count)]
    state = publisher(jobs=fresh, recovery=recovery)
    assert publish(state) == 100
    dispatched = {call.args[1] for call in state.queue.enqueue.call_args_list}
    assert len(dispatched & {str(item.id) for item in fresh}) == 75
    assert len(dispatched & {str(item.id) for item in recovery}) == 25


@pytest.mark.parametrize(
    "fresh_count,recovery_count", [(1000, 0), (0, 1000), (10, 1000), (1000, 10)]
)
def test_unused_fresh_or_recovery_share_fills_the_same_batch(fresh_count, recovery_count):
    fresh = [job() for _ in range(fresh_count)]
    recovery = [job() for _ in range(recovery_count)]
    state = publisher(jobs=fresh, recovery=recovery)
    assert publish(state) == 100
    dispatched = {call.args[1] for call in state.queue.enqueue.call_args_list}
    fresh_dispatched = len(dispatched & {str(item.id) for item in fresh})
    recovery_dispatched = len(dispatched & {str(item.id) for item in recovery})
    assert fresh_dispatched == min(fresh_count, max(75, 100 - recovery_count))
    assert recovery_dispatched == 100 - fresh_dispatched
    state.queue.connection.incr.assert_not_called()


@pytest.mark.parametrize("queue_name", ["ingestion", "source-analysis"])
def test_one_free_slot_recovers_work_every_fourth_contended_refill(queue_name):
    counter = Mock(side_effect=[1, 2, 3, 4])
    for turn in range(1, 5):
        fresh, recovery = job(), job()
        state = publisher(jobs=[fresh], recovery=[recovery], count=199)
        state.queue.connection.incr = counter
        assert publish(state, queue_name=queue_name) == 1
        selected = recovery if turn == 4 else fresh
        assert state.queue.enqueue.call_args.args[1] == str(selected.id)
    assert counter.call_count == 4
    counter.assert_called_with(
        f"devfeed:partner-dispatch:recovery-turn:{queue_name}:{PartnerPipelineJob.__tablename__}"
    )


def test_shared_ai_queue_keeps_independent_recovery_turns_for_both_models():
    turns = {}

    def increment(key):
        turns[key] = turns.get(key, 0) + 1
        return turns[key]

    counter = Mock(side_effect=increment)
    for turn in range(1, 5):
        for model in (PartnerPipelineJob, PartnerEvaluation):
            fresh, recovery = job(), job()
            state = publisher(jobs=[fresh], recovery=[recovery], count=199)
            state.queue.connection.incr = counter
            assert publish(state, queue_name="source-analysis", model=model) == 1
            selected = recovery if turn == 4 else fresh
            assert state.queue.enqueue.call_args.args[1] == str(selected.id)
    assert turns == {
        f"devfeed:partner-dispatch:recovery-turn:source-analysis:{model.__tablename__}": 4
        for model in (PartnerPipelineJob, PartnerEvaluation)
    }


@pytest.mark.parametrize("fresh_only", [True, False])
def test_one_free_slot_without_competing_work_needs_no_turn_counter(fresh_only):
    pending = job()
    state = publisher(
        jobs=[pending] if fresh_only else [],
        recovery=[] if fresh_only else [pending],
        count=199,
    )
    assert publish(state) == 1
    assert state.queue.enqueue.call_args.args[1] == str(pending.id)
    state.queue.connection.incr.assert_not_called()


def test_turn_counter_failure_rolls_back_and_closes_queue():
    fresh, recovery = job(), job()
    state = publisher(jobs=[fresh], recovery=[recovery], count=199)
    state.queue.connection.incr.side_effect = RuntimeError("redis unavailable")
    with pytest.raises(RuntimeError, match="redis unavailable"):
        publish(state)
    assert fresh.dispatched_at is None and recovery.dispatched_at is None
    assert state.transaction.rolled_back
    state.queue.enqueue.assert_not_called()
    state.queue.connection.close.assert_called_once()


def test_delivery_failure_rolls_back_claims_and_closes_queue(caplog):
    first, second = job(), job()
    state = publisher(jobs=[first, second])
    state.queue.enqueue.side_effect = [None, RuntimeError("redis unavailable")]
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="redis unavailable"):
        publish(state)
    assert first.dispatched_at == state.outbox.now
    assert second.dispatched_at is None
    assert state.transaction.rolled_back
    assert not state.transaction.committed
    state.queue.connection.close.assert_called_once()
    assert not any(record.message == "partner_pipeline_dispatched" for record in caplog.records)


def test_queue_depth_failure_closes_connection(monkeypatch):
    state = publisher()
    monkeypatch.setattr(
        type(state.queue),
        "count",
        PropertyMock(side_effect=RuntimeError("redis down")),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="redis down"):
        publish(state)
    state.queue.connection.close.assert_called_once()
    state.session.scalars.assert_not_called()
    assert state.transaction.rolled_back


def test_dispatch_events_are_emitted_only_after_commit(monkeypatch):
    pending = job()
    state = publisher(jobs=[pending])
    record = Mock(
        side_effect=lambda *_args, **_kwargs: state.transaction.committed or pytest.fail()
    )
    monkeypatch.setattr("devfeed_aggregator.partner_outbox.logger.info", record)
    assert publish(state) == 1
    record.assert_called_once_with(
        "partner_pipeline_dispatched", extra={"job_id": str(pending.id), "queue": "ingestion"}
    )


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_rejected_before_opening_resources(limit):
    state = publisher()
    with pytest.raises(ValueError, match="limit must be positive"):
        publish(state, limit=limit)
    state.lookup.assert_not_called()
    state.outbox.factory.begin.assert_not_called()


def test_unknown_queue_rejected_before_opening_resources():
    state = publisher()
    with pytest.raises(ValueError, match="Unsupported partner worker queue"):
        publish(state, queue_name="source-discovery")
    state.lookup.assert_not_called()
    state.outbox.factory.begin.assert_not_called()


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_batch_size_rejected(batch_size):
    with pytest.raises(ValueError, match="batch size must be positive"):
        publisher(batch_size=batch_size)

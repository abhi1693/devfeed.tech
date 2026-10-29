"""Partner worker lease fencing, bounded diagnostics and queue routing."""

import uuid
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_aggregator import partner_product_sync as products
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_aggregator import partner_tasks as evaluations
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import PartnerConnection, utcnow


def transaction_factory(session):
    session.begin_nested.side_effect = lambda: nullcontext()
    factory = Mock(return_value=nullcontext(session))
    factory.begin.side_effect = lambda: nullcontext(session)
    return factory


def leased_job(**changes):
    return NS(
        **{
            "id": uuid.uuid4(),
            "product_id": uuid.uuid4(),
            "parent_id": uuid.uuid4(),
            "provider": "nick-launches",
            "operation": "sync_product",
            "external_id": "checker",
            "status": "queued",
            "attempts": 0,
            "payload": {},
            "snapshot": {"articles": []},
            "available_at": utcnow(),
            "lease_token": None,
            "lease_until": None,
            "dispatched_at": None,
            "finished_at": None,
            "error": None,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "mode", ["disabled", "missing", "stale", "exhausted", "success", "error", "lost", "changed"]
)
def test_evaluation_rejects_stale_or_unowned_results(monkeypatch, mode):
    session = Mock()
    factory = transaction_factory(session)
    job = leased_job(attempts=3 if mode == "exhausted" else 0)
    session.scalar.return_value = None if mode == "missing" else job
    monkeypatch.setattr(evaluations, "get_settings", lambda: NS(ai_enabled=mode != "disabled"))
    publish = Mock()
    monkeypatch.setattr(evaluations, "save_evaluation_matches", publish)
    current = Mock(
        side_effect=[True, False] if mode == "changed" else None, return_value=mode != "stale"
    )
    monkeypatch.setattr(evaluations, "snapshot_current", current)
    validated = {"decisions": [{"relevant": True}]}
    monkeypatch.setattr(evaluations, "validate_result", lambda *_: validated)

    def assess(_):
        if mode == "error":
            raise RuntimeError("private provider response")
        if mode == "lost":
            job.lease_token = uuid.uuid4()
        return validated

    evaluations.process_evaluation(str(job.id), factory=factory, assessor=assess)
    if mode == "disabled":
        factory.begin.assert_not_called()
    elif mode == "missing":
        assert job.attempts == 0
    elif mode in {"stale", "exhausted", "changed"}:
        assert job.status == "failed" and job.lease_token is None
    elif mode == "success":
        assert job.status == "succeeded" and job.result == validated and job.lease_token is None
        publish.assert_called_once_with(session, job)
    elif mode == "error":
        assert job.status == "queued" and "private provider response" not in job.error
    else:
        assert job.status == "running" and not hasattr(job, "result")


@pytest.mark.parametrize("enabled,failed", [(False, False), (True, False), (True, True)])
def test_evaluation_dispatch_closes_queue_even_on_delivery_failure(monkeypatch, enabled, failed):
    session, queue = Mock(), Mock()
    queue.count = 0
    queue.name = "default"
    job = leased_job()
    session.scalars.side_effect = [NS(all=lambda: [job]), NS(all=lambda: [])]
    monkeypatch.setattr(
        evaluations, "get_settings", lambda: NS(ai_enabled=enabled, scheduler_batch_size=100)
    )
    monkeypatch.setattr(evaluations, "get_queue", lambda _: queue)
    if failed:
        queue.enqueue.side_effect = RuntimeError("redis unavailable")
        with pytest.raises(RuntimeError):
            evaluations.dispatch_partner_evaluations(transaction_factory(session))
        assert job.dispatched_at is None
    else:
        assert evaluations.dispatch_partner_evaluations(transaction_factory(session)) == int(
            enabled
        )
        assert queue.enqueue.call_count == int(enabled)
    assert queue.connection.close.call_count == int(enabled)


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "disabled",
        "archived",
        "parent-failed",
        "generation",
        "exhausted",
        "success",
        "identity",
        "http",
        "database",
        "lost",
        "paused",
        "archived-after-read",
    ],
)
def test_product_sync_preserves_generation_and_fences_results(monkeypatch, mode):
    session = Mock()
    factory = transaction_factory(session)
    candidate = {"external_id": "checker", "listing_url": "https://nicklaunches.com/checker"}
    job = leased_job(payload={"candidate": candidate}, attempts=3 if mode == "exhausted" else 0)
    parent = leased_job(
        id=job.parent_id,
        status="cancelled" if mode == "parent-failed" else "running",
        payload={"connection_revision": 1, "generation": str(uuid.uuid4())},
    )
    connection = NS(
        enabled=mode != "disabled",
        archived_at=utcnow() if mode == "archived" else None,
        sync_revision=2 if mode == "generation" else 1,
        provider="nick-launches",
        connector={},
    )
    session.scalar.return_value = None if mode == "missing" else job
    session.get.side_effect = lambda model, identifier: (
        connection if model is PartnerConnection else parent if identifier == parent.id else job
    )
    monkeypatch.setattr(pipeline, "pending_redirect_proofs", lambda *_: [])
    monkeypatch.setattr(products, "ProductCandidate", Mock())
    products.ProductCandidate.model_validate.return_value = NS(external_id="checker")
    canonical = NS(id=uuid.uuid4())
    writer = Mock(return_value=canonical)
    if mode == "database":
        writer.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(products, "upsert_listing", writer)

    def read(*_):
        if mode == "http":
            raise FeedError("private response", status=429, retryable=True, retry_after=120)
        if mode == "lost":
            job.lease_token = uuid.uuid4()
        if mode == "paused":
            connection.enabled = False
        if mode == "archived-after-read":
            connection.archived_at = utcnow()
        return NS(provider="other" if mode == "identity" else job.provider, external_id="checker")

    products.process_product_sync(job.id, factory, product_reader=read)
    if mode == "missing":
        assert job.attempts == 0
    elif mode in {
        "disabled",
        "archived",
        "parent-failed",
        "generation",
        "exhausted",
        "identity",
        "paused",
        "archived-after-read",
    }:
        assert job.status == "failed" and job.lease_token is None
    elif mode == "success":
        assert job.status == "succeeded" and job.product_id == canonical.id
    elif mode in {"http", "database"}:
        assert job.status == "queued" and "private response" not in job.error
        if mode == "http":
            assert (job.available_at - utcnow()).total_seconds() > 110
    else:
        assert job.status == "running" and writer.call_count == 0


@pytest.mark.parametrize(
    "operation,enabled,failed",
    [
        ("sync", False, False),
        ("sync_product", True, False),
        ("assess", True, False),
        ("sync", True, True),
    ],
)
@pytest.mark.parametrize("turn", [1, 2])
def test_pipeline_routes_api_work_to_standard_worker_and_closes_queue(
    monkeypatch, operation, enabled, failed, turn
):
    session, queue = Mock(), Mock()
    queue.count = 0
    queue.name = "default"
    queue.connection.get.return_value = turn - 1
    job = leased_job(operation=operation)

    def claims(statement):
        operations = next(
            value
            for key, value in statement.compile().params.items()
            if key.startswith("operation_") and isinstance(value, list)
        )
        selected = operation in operations and "dispatched_at IS NOT NULL" not in str(statement)
        return NS(all=lambda: [job] if selected else [])

    session.scalars.side_effect = claims
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(pipeline, "lock_catalog", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=enabled, scheduler_batch_size=100)
    )
    lookup = Mock(return_value=queue)
    monkeypatch.setattr(pipeline, "get_queue", lookup)
    if failed:
        queue.enqueue.side_effect = RuntimeError("redis unavailable")
        with pytest.raises(RuntimeError):
            pipeline.dispatch_partner_pipeline(transaction_factory(session))
        assert job.dispatched_at is None
    else:
        assert pipeline.dispatch_partner_pipeline(transaction_factory(session)) == 1
        assert job.dispatched_at is not None
    expected = "source-analysis" if operation == "assess" else "ingestion"
    assert [call.args[0] for call in lookup.call_args_list] == ["partner-pipeline"] * (
        1 + 2 * int(enabled)
    )
    assert queue.enqueue.call_args.kwargs["meta"] == {"devfeed_ai": operation == "assess"}
    assert expected in queue.enqueue.call_args.kwargs["job_id"]
    assert queue.connection.close.call_count == 1 + 2 * int(enabled)


def test_ai_enabled_pipeline_with_no_due_work_never_opens_redis(monkeypatch):
    session = Mock()
    session.scalar.return_value = False
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=True, scheduler_batch_size=100)
    )
    lookup = Mock()
    monkeypatch.setattr(pipeline, "get_queue", lookup)
    assert pipeline.dispatch_partner_pipeline(transaction_factory(session)) == 0
    session.scalar.assert_called_once()
    assert "EXISTS" in str(session.scalar.call_args.args[0])
    lookup.assert_not_called()


def test_lane_turn_failure_closes_redis_before_any_publication(monkeypatch):
    session, queue = Mock(), Mock()
    queue.name = "catalog-pipeline"
    queue.count = 0
    queue.connection.get.side_effect = RuntimeError("redis unavailable")
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=True, scheduler_batch_size=100)
    )
    monkeypatch.setattr(pipeline, "get_queue", Mock(return_value=queue))
    with pytest.raises(RuntimeError, match="redis unavailable"):
        pipeline.dispatch_partner_pipeline(transaction_factory(session))
    queue.connection.close.assert_called_once()
    queue.enqueue.assert_not_called()


@pytest.mark.parametrize("mode", ["busy", "full", "empty-refill", "counter-write-failure"])
def test_pipeline_turn_guards_and_failed_counter_cleanup(monkeypatch, mode):
    session, queue = Mock(), Mock()
    session.scalar.side_effect = [True, mode != "busy"]
    queue.name = "catalog-pipeline"
    queue.count = 200 if mode == "full" else 0
    queue.connection.get.return_value = None
    outbox = Mock()
    outbox.publish.return_value = 1 if mode == "counter-write-failure" else 0
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=True, scheduler_batch_size=100)
    )
    monkeypatch.setattr(pipeline, "get_queue", Mock(return_value=queue))
    monkeypatch.setattr(pipeline, "PartnerOutbox", Mock(return_value=outbox))
    if mode == "counter-write-failure":
        queue.connection.incr.side_effect = RuntimeError("redis unavailable")
        with pytest.raises(RuntimeError, match="redis unavailable"):
            pipeline.dispatch_partner_pipeline(transaction_factory(session))
        queue.connection.incr.assert_called_once_with(
            "devfeed:partner-dispatch:lane-turn:catalog-pipeline"
        )
    else:
        assert pipeline.dispatch_partner_pipeline(transaction_factory(session)) == 0
        queue.connection.incr.assert_not_called()
    assert outbox.publish.call_count == (0 if mode in {"busy", "full"} else 2)
    queue.connection.close.assert_called_once()


@pytest.mark.parametrize("mode", ["success", "counter-failure", "commit-failure"])
def test_shared_lane_dispatch_logs_wait_for_the_outer_commit(monkeypatch, mode):
    from devfeed_aggregator import partner_outbox

    session, queue = Mock(), Mock()
    session.begin_nested.side_effect = lambda: nullcontext()
    job = leased_job(operation="sync")
    session.scalars.side_effect = [
        NS(all=lambda: [job]),
        NS(all=lambda: []),
        NS(all=lambda: []),
        NS(all=lambda: []),
    ]
    queue.name, queue.count = "default", 0
    queue.connection.get.return_value = None
    if mode == "counter-failure":
        queue.connection.incr.side_effect = RuntimeError("counter failed")
    state = NS(committed=False)

    @contextmanager
    def begin():
        yield session
        if mode == "commit-failure":
            raise RuntimeError("commit failed")
        state.committed = True

    factory = Mock()
    factory.begin.side_effect = begin
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=True, scheduler_batch_size=100)
    )
    monkeypatch.setattr(pipeline, "get_queue", Mock(return_value=queue))
    record = Mock(side_effect=lambda *_args, **_kwargs: state.committed or pytest.fail())
    monkeypatch.setattr(partner_outbox.logger, "info", record)
    if mode == "success":
        assert pipeline.dispatch_partner_pipeline(factory) == 1
        record.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="failed"):
            pipeline.dispatch_partner_pipeline(factory)
        assert not state.committed
        record.assert_not_called()
    queue.enqueue.assert_called_once()
    assert queue.connection.close.call_count == 3


def test_pipeline_turn_body_failure_cannot_advance_or_log_uncommitted_work(monkeypatch):
    from devfeed_aggregator import partner_outbox
    from devfeed_core.models import PartnerPipelineJob
    from sqlalchemy import select

    session, queue = Mock(), Mock()
    queue.name, queue.count = "default", 0
    queue.connection.get.return_value = None
    monkeypatch.setattr(pipeline, "get_queue", Mock(return_value=queue))
    record = Mock()
    monkeypatch.setattr(partner_outbox.logger, "info", record)
    with (
        pytest.raises(RuntimeError, match="cancelled"),
        pipeline._pipeline_turn(
            transaction_factory(session), select(PartnerPipelineJob), 100
        ) as turn,
    ):
        turn.published = 1
        turn.pending_logs.append(("partner_pipeline_dispatched", {"job_id": "never-committed"}))
        raise RuntimeError("cancelled")
    queue.connection.incr.assert_not_called()
    record.assert_not_called()
    queue.connection.close.assert_called_once()


@pytest.mark.parametrize("network_fails", [False, True])
def test_pipeline_shares_the_batch_deadline_without_starving_the_second_lane(
    monkeypatch, network_fails
):
    from devfeed_aggregator import partner_outbox

    clock = [0.0]
    monkeypatch.setattr(pipeline, "time", NS(monotonic=lambda: clock[0]))
    monkeypatch.setattr(partner_outbox, "time", NS(monotonic=lambda: clock[0]))
    session, queue = Mock(), Mock()
    queue.name, queue.count = "default", 0
    queue.connection.get.return_value = None
    jobs = [leased_job(operation=operation) for operation in ["sync_product"] * 3 + ["assess"] * 3]

    def claims(statement):
        operations = next(
            value
            for key, value in statement.compile().params.items()
            if key.startswith("operation_") and isinstance(value, list)
        )
        fresh = "dispatched_at IS NOT NULL" not in str(statement)
        selected = [job for job in jobs if fresh and job.operation in operations]
        return NS(all=lambda: selected)

    def enqueue(*_args, **kwargs):
        clock[0] += 0.6
        if network_fails and not kwargs["meta"]["devfeed_ai"]:
            clock[0] += 0.6
            raise RuntimeError("network publication failed")

    session.scalars.side_effect = claims
    queue.enqueue.side_effect = enqueue
    monkeypatch.setattr(pipeline, "schedule_partner_syncs", Mock())
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: NS(ai_enabled=True, scheduler_batch_size=100)
    )
    monkeypatch.setattr(pipeline, "get_queue", Mock(return_value=queue))
    if network_fails:
        with pytest.raises(RuntimeError, match="network publication failed"):
            pipeline.dispatch_partner_pipeline(transaction_factory(session))
        assert all(job.dispatched_at is None for job in jobs[:3])
    else:
        assert pipeline.dispatch_partner_pipeline(transaction_factory(session)) == 3
        assert [job.dispatched_at is not None for job in jobs[:3]] == [True, True, False]
    assert [job.dispatched_at is not None for job in jobs[3:]] == [True, False, False]
    queue.connection.incr.assert_called_once_with("devfeed:partner-dispatch:lane-turn:default")


@pytest.mark.parametrize("created", [False, True])
def test_scheduling_keeps_future_connections_and_qualifies_products(monkeypatch, created):
    from datetime import timedelta

    session = Mock()
    job = leased_job(operation="sync")
    due = NS(
        account_id=None,
        sync_interval_minutes=360,
        last_sync_at=None,
        next_sync_at=utcnow() - timedelta(seconds=1),
    )
    future = NS(
        account_id=None,
        sync_interval_minutes=360,
        last_sync_at=None,
        next_sync_at=utcnow() + timedelta(hours=1),
    )
    product = NS(id=uuid.uuid4(), revision=1, assessment_revision=0)
    session.scalars.side_effect = [NS(all=lambda: [due, future]), NS(all=lambda: [product])]
    session.new = [job] if created else []
    request = Mock(return_value=job)
    assess = Mock()
    monkeypatch.setattr(pipeline, "request_sync", request)
    monkeypatch.setattr(pipeline, "request_assessment", assess)
    monkeypatch.setattr(pipeline, "lock_catalog", Mock())
    pipeline.schedule_partner_syncs(transaction_factory(session))
    request.assert_called_once_with(session, due)
    assess.assert_called_once_with(session, product, renewal=False)
    session.flush.assert_called_once()
    session.get.assert_not_called()


@pytest.mark.parametrize(
    ("tier", "minutes", "expected"),
    [
        ("bronze", 1440, 1440),
        ("bronze", 60, 1440),
        ("silver", 1440, 1440),
        ("silver", 720, 1440),
        ("gold", 720, 720),
        ("gold", 360, 1440),
        ("platinum", 360, 360),
        ("platinum", 60, 1440),
        ("diamond", 60, 60),
        ("diamond", 120, 1440),
    ],
)
def test_connection_sync_interval_uses_current_account_tier(tier, minutes, expected):
    from devfeed_core.models import PartnerAccount

    account_id = uuid.uuid4()
    session = Mock()
    session.get.return_value = NS(tier=tier)
    connection = NS(account_id=account_id, sync_interval_minutes=minutes)
    assert pipeline.connection_sync_interval(session, connection) == expected
    session.get.assert_called_once_with(PartnerAccount, account_id)


def test_connection_sync_interval_preserves_unowned_administrator_schedule():
    session = Mock()
    connection = NS(account_id=None, sync_interval_minutes=15)
    assert pipeline.connection_sync_interval(session, connection) == 15
    session.get.assert_not_called()

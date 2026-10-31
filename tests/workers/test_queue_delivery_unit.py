"""RQ delivery recovery protocol. Redis Lua membership/atomicity is not emulated."""

import zlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import queue as queues
from rq import Queue
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer


@pytest.fixture
def runtime(monkeypatch):
    connection = Mock()
    queue = queues.DurableQueue("ingestion", connection=connection, serializer=JSONSerializer)
    base = Mock()
    monkeypatch.setattr(Queue, "enqueue", base)
    monkeypatch.setattr(queues, "inject_context", lambda: {})
    fetch = Mock()
    monkeypatch.setattr(Job, "fetch", fetch)
    return SimpleNamespace(queue=queue, connection=connection, base=base, fetch=fetch)


def existing(runtime, status, *, origin="ingestion"):
    row = Job(id="delivery-123", connection=runtime.connection, serializer=JSONSerializer)
    row.origin = origin
    row.meta = {"devfeed_queue": origin} if origin in queues.ROUTABLE_QUEUES else {}
    row.get_status = Mock(return_value=status)
    row.delete = Mock()
    runtime.fetch.return_value = row
    return row


def enqueue(runtime, **changes):
    return runtime.queue.enqueue(
        "devfeed_aggregator.tasks.ingest", "job-123", unique=True, job_id="delivery-123", **changes
    )


def test_new_delivery_merges_trace_context_without_discarding_existing_metadata(
    runtime, monkeypatch
):
    monkeypatch.setattr(queues, "inject_context", lambda: {"traceparent": "00-valid-context"})
    delivery = object()
    runtime.base.return_value = delivery
    assert enqueue(runtime, meta={"owner": "outbox"}) is delivery
    assert runtime.base.call_args.kwargs["meta"] == {
        "owner": "outbox",
        "devfeed_trace": {"traceparent": "00-valid-context"},
    }
    runtime.fetch.assert_not_called()


@pytest.mark.parametrize("origin", ["ingestion", "source-discovery", "unrelated-service"])
def test_duplicate_queued_delivery_is_retained_and_recovery_targets_its_original_queue(
    runtime, origin
):
    row = existing(runtime, JobStatus.QUEUED, origin=origin)
    runtime.base.side_effect = DuplicateJobError()
    assert enqueue(runtime) is row
    row.get_status.assert_called_once_with(refresh=True)
    row.delete.assert_not_called()
    if origin == "unrelated-service":
        runtime.connection.eval.assert_not_called()
    else:
        key = f"rq:queue:{origin}"
        runtime.connection.eval.assert_called_once_with(
            queues.RESTORE_QUEUED,
            4,
            row.key,
            key,
            f"{key}:intermediate",
            "rq:queues",
            row.id,
            origin,
        )
    assert runtime.base.call_count == 1


@pytest.mark.parametrize("status", [JobStatus.STARTED, JobStatus.SCHEDULED, JobStatus.DEFERRED])
def test_duplicate_active_or_scheduled_delivery_is_not_deleted_or_requeued(runtime, status):
    row = existing(runtime, status)
    runtime.base.side_effect = DuplicateJobError()
    assert enqueue(runtime) is row
    row.delete.assert_not_called()
    runtime.connection.eval.assert_not_called()
    assert runtime.base.call_count == 1


@pytest.mark.parametrize(
    "status", [JobStatus.FAILED, JobStatus.FINISHED, JobStatus.STOPPED, JobStatus.CANCELED]
)
def test_terminal_broker_delivery_can_be_replaced_for_still_due_durable_work(runtime, status):
    row = existing(runtime, status)
    replacement = object()
    runtime.base.side_effect = [DuplicateJobError(), replacement]
    assert enqueue(runtime) is replacement
    row.delete.assert_called_once()
    runtime.connection.eval.assert_not_called()
    assert runtime.base.call_count == 2


def test_delivery_removed_between_duplicate_error_and_lookup_can_be_enqueued_again(runtime):
    replacement = object()
    runtime.base.side_effect = [DuplicateJobError(), replacement]
    runtime.fetch.side_effect = NoSuchJobError()
    assert enqueue(runtime) is replacement
    assert runtime.base.call_count == 2
    runtime.connection.eval.assert_not_called()


def test_duplicate_without_unique_contract_propagates_the_original_error(runtime):
    error = DuplicateJobError()
    runtime.base.side_effect = error
    with pytest.raises(DuplicateJobError) as caught:
        runtime.queue.enqueue("devfeed_aggregator.tasks.ingest", "job-123", job_id="delivery-123")
    assert caught.value is error
    runtime.fetch.assert_not_called()


def test_unknown_queue_is_rejected_before_creating_a_redis_connection(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(queues, "create_redis", connect)
    with pytest.raises(ValueError, match="Unknown worker queue"):
        queues.get_queue("unknown-private-queue")
    connect.assert_not_called()


def test_publication_client_has_an_independent_single_attempt_io_budget(monkeypatch):
    config = queues.get_settings()
    monkeypatch.setattr(config, "job_queue_overrides", {"partner-pipeline": "catalog"})
    monkeypatch.setattr(
        config, "redis_url", "redis://ignored:6379/15?socket_timeout=85&socket_connect_timeout=85"
    )
    publication = queues.get_publication_queue("partner-pipeline")
    ordinary = queues.get_queue("partner-pipeline")
    try:
        assert publication.name == ordinary.name == "catalog"
        assert publication.logical_name == "partner-pipeline"
        assert publication.redirect_queue is queues.get_publication_queue
        assert publication.serializer is JSONSerializer
        assert publication.connection.connection_pool is not ordinary.connection.connection_pool
        options = publication.connection.connection_pool.connection_kwargs
        assert options["socket_timeout"] == options["socket_connect_timeout"] == 1
        assert options["retry"].get_retries() == 0
        assert options["db"] == 15
        assert ordinary.connection.connection_pool.connection_kwargs["socket_timeout"] == 85
    finally:
        publication.connection.close()
        ordinary.connection.close()


@pytest.mark.parametrize(
    "handler,logical",
    [
        ("devfeed_aggregator.partner_sync_tasks.process_pipeline", "partner-pipeline"),
        ("devfeed_aggregator.partner_tasks.process_evaluation", "partner-evaluation"),
        ("devfeed_aggregator.recommendation_tasks.refresh", "recommendations"),
    ],
)
def test_handler_redirection_retains_its_publication_client_factory(runtime, handler, logical):
    runtime.queue.logical_name = "ingestion"
    target = Mock()
    target.enqueue.return_value = object()
    redirect = Mock(return_value=target)
    runtime.queue.redirect_queue = redirect
    assert runtime.queue.enqueue(handler, "id", unique=True) is target.enqueue.return_value
    redirect.assert_called_once_with(logical)
    target.enqueue.assert_called_once_with(handler, "id", unique=True)
    target.connection.close.assert_called_once()
    runtime.base.assert_not_called()


def test_owned_delivery_on_an_old_custom_route_is_repaired_in_place(runtime):
    row = existing(runtime, JobStatus.QUEUED, origin="retired-dedicated")
    row.meta = {"devfeed_queue": "article-analysis"}
    runtime.base.side_effect = DuplicateJobError()
    assert enqueue(runtime) is row
    assert runtime.connection.eval.call_args.args[-1] == "retired-dedicated"
    assert runtime.base.call_count == 1
    row.delete.assert_not_called()


def test_logical_metadata_cannot_be_spoofed_by_the_enqueue_caller(runtime):
    runtime.queue.logical_name = "source-analysis"
    enqueue(runtime, meta={"devfeed_queue": "ingestion", "owner": "outbox"})
    assert runtime.base.call_args.kwargs["meta"] == {
        "devfeed_queue": "source-analysis",
        "owner": "outbox",
    }


@pytest.mark.parametrize("refused", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_notification_refresh_returns_exact_stored_args_even_when_worker_wins(
    runtime, refused, compressed
):
    row = existing(runtime, JobStatus.QUEUED, origin="notifications")
    row.id = "devfeed-notifications-batch-test"
    row.func_name = "devfeed_notifications.delivery.deliver_notification"
    row.args, row.kwargs = ["old-envelope"], {}
    runtime.base.side_effect = DuplicateJobError()
    expected = ["old-envelope"] if refused else ["fresh-envelope"]
    stored = JSONSerializer.dumps((row.func_name, None, expected, {}))
    if compressed:
        stored = zlib.compress(stored)
    runtime.connection.eval.return_value = stored
    assert (
        runtime.queue.enqueue(
            row.func_name,
            "fresh-envelope",
            job_id=row.id,
            unique=True,
            refresh_queued_payload=True,
        )
        is row
    )
    assert row.args == expected
    assert "refresh_queued_payload" not in runtime.base.call_args.kwargs
    script, keys, job_key, queue_key, intermediate, queues_key, identity, origin, data = (
        runtime.connection.eval.call_args.args
    )
    assert (script, keys, job_key, queue_key, intermediate, queues_key, identity, origin) == (
        queues.REFRESH_QUEUED_ARGS,
        4,
        row.key,
        "rq:queue:notifications",
        "rq:queue:notifications:intermediate",
        "rq:queues",
        row.id,
        row.origin,
    )
    assert JSONSerializer.loads(zlib.decompress(data))[2] == ["fresh-envelope"]


def test_notification_refresh_disappearance_rolls_back_the_publisher(runtime):
    row = existing(runtime, JobStatus.QUEUED, origin="notifications")
    row.id = "devfeed-notifications-batch-test"
    row.func_name, row.args, row.kwargs = (
        "devfeed_notifications.delivery.deliver_notification",
        ["old"],
        {},
    )
    runtime.base.side_effect = DuplicateJobError()
    runtime.connection.eval.return_value = None
    with pytest.raises(NoSuchJobError, match="disappeared"):
        runtime.queue.enqueue(
            row.func_name, "new", job_id=row.id, unique=True, refresh_queued_payload=True
        )
    assert row.args == ["old"]


@pytest.mark.parametrize(
    "handler,identity",
    [
        ("other", "devfeed-notifications-batch-test"),
        ("devfeed_notifications.delivery.deliver_notification", "singleton"),
    ],
)
def test_payload_refresh_is_restricted_to_notification_batch_carriers(runtime, handler, identity):
    with pytest.raises(ValueError, match="Only notification batch carriers"):
        runtime.queue.enqueue(handler, "new", job_id=identity, refresh_queued_payload=True)
    runtime.base.assert_not_called()


@pytest.mark.parametrize("changed", ["handler", "owner"])
def test_notification_refresh_rejects_a_different_carrier_owner(runtime, changed):
    row = existing(runtime, JobStatus.QUEUED, origin="notifications")
    row.id = "devfeed-notifications-batch-test"
    row.func_name = (
        "other" if changed == "handler" else "devfeed_notifications.delivery.deliver_notification"
    )
    if changed == "owner":
        row.meta["devfeed_queue"] = "ingestion"
    runtime.base.side_effect = DuplicateJobError()
    with pytest.raises(ValueError, match="handler changed"):
        runtime.queue.enqueue(
            "devfeed_notifications.delivery.deliver_notification",
            "new",
            job_id=row.id,
            unique=True,
            refresh_queued_payload=True,
        )
    runtime.connection.eval.assert_not_called()

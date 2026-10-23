"""RQ delivery recovery protocol. Redis Lua membership/atomicity is not emulated."""

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

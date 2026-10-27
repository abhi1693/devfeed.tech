"""Background discovery route and broker-cleanup protocol over admitted job reads."""

import json
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import codex_client
from devfeed_aggregator import discovery_tasks as worker
from devfeed_core import discovery
from devfeed_core.models import SourceDiscoveryJob

from tests.support.source_readiness import NOW, configuration


class Factory:
    def __init__(self, read, write):
        self.read, self.write, self.open = read, write, False

    @contextmanager
    def __call__(self):
        self.open = True
        try:
            yield self.read
        finally:
            self.open = False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield self.write
        finally:
            self.open = False


@pytest.fixture
def runtime(monkeypatch):
    settings = configuration(ai_enabled=True)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "utcnow", lambda: NOW)
    read, write = Mock(), Mock()
    factory = Factory(read, write)
    queues = []

    def get_queue(name):
        assert not factory.open
        queue = Mock(name=name)
        queues.append((name, queue))
        return queue

    monkeypatch.setattr(worker, "get_queue", get_queue)
    return SimpleNamespace(
        settings=settings, read=read, write=write, factory=factory, queues=queues
    )


def discovered_job(stage="discover", **changes):
    return SourceDiscoveryJob(
        **{
            "id": uuid.uuid4(),
            "candidate_id": uuid.uuid4(),
            "stage": stage,
            "status": "queued",
            "attempts": 0,
            "result": {"background": True},
            "created_at": NOW - timedelta(minutes=10),
            "available_at": NOW - timedelta(minutes=9),
            "lease_token": None,
            "lease_until": None,
            "dispatched_at": None,
            **changes,
        }
    )


@pytest.mark.parametrize("stage", ["discover", "assess"])
@pytest.mark.parametrize("state", ["queued", "expired", "delayed", "leased", "missing", "finished"])
def test_dispatch_rechecks_due_or_expired_work_and_closes_each_queue_connection(
    runtime, stage, state
):
    job = discovered_job(stage)
    if state == "expired":
        job.status, job.attempts, job.lease_token, job.lease_until = (
            "running",
            1,
            uuid.uuid4(),
            NOW - timedelta(seconds=1),
        )
    elif state == "delayed":
        job.available_at = NOW + timedelta(minutes=2)
    elif state == "leased":
        job.status, job.attempts, job.lease_token, job.lease_until = (
            "running",
            1,
            uuid.uuid4(),
            NOW + timedelta(minutes=2),
        )
    elif state == "finished":
        job.status, job.finished_at, job.attempts = "succeeded", NOW, 1
    runtime.read.execute.return_value = [(job.id, stage)]
    dispatched = state in {"queued", "expired"}
    runtime.write.scalars.return_value.all.return_value = [job] if dispatched else []
    assert worker.dispatch_discovery(runtime.factory) == int(dispatched)
    queue_name, queue = runtime.queues[0]
    assert queue_name == ("source-analysis" if stage == "assess" else "source-discovery")
    if dispatched:
        queue.enqueue.assert_called_once_with(
            "devfeed_aggregator.discovery_tasks.process_candidate",
            str(job.id),
            job_id=f"source-discovery-{job.id}"
            if stage == "assess"
            else f"source-discovery-v2-{job.id}",
            unique=True,
            job_timeout=300,
            result_ttl=0,
            failure_ttl=3600,
        )
        assert job.dispatched_at == NOW
    else:
        queue.enqueue.assert_not_called()
        assert job.dispatched_at is None
    queue.connection.close.assert_called_once()


def test_assessment_disabled_after_candidate_read_does_not_publish_an_ai_delivery(runtime):
    job = discovered_job("assess")

    def read(_statement):
        runtime.settings.ai_enabled = False
        return [(job.id, "assess")]

    runtime.read.execute.side_effect = read
    assert worker.dispatch_discovery(runtime.factory) == 0
    assert runtime.queues == []
    runtime.write.scalars.assert_not_called()


def test_enqueue_failure_preserves_dispatch_marker_and_closes_the_broker_connection(
    runtime, monkeypatch
):
    job = discovered_job()
    runtime.read.execute.return_value = [(job.id, job.stage)]
    runtime.write.scalars.return_value.all.return_value = [job]
    queue = Mock()
    queue.enqueue.side_effect = RuntimeError("Redis disconnected")
    monkeypatch.setattr(worker, "get_queue", lambda _: queue)
    with pytest.raises(RuntimeError, match="Redis disconnected"):
        worker.dispatch_discovery(runtime.factory)
    assert job.dispatched_at is None
    queue.connection.close.assert_called_once()


@pytest.mark.parametrize("enabled", [False, True])
def test_candidate_worker_forwards_the_uuid_and_current_ai_admission_to_discovery(
    runtime, monkeypatch, enabled
):
    runtime.settings.ai_enabled = enabled
    run = Mock(return_value={"status": "complete"})
    monkeypatch.setattr(discovery, "run_one", run)
    identifier = uuid.uuid4()
    assert worker.process_candidate(str(identifier)) == {"status": "complete"}
    run.assert_called_once_with(
        target_job_id=identifier, allow_ai=enabled, assessor=worker.assess_sample
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_assessment_provider_receives_the_real_quality_schema_and_indexed_untrusted_sample(
    runtime, monkeypatch, enabled
):
    runtime.settings.ai_enabled = enabled
    monkeypatch.setattr("devfeed_core.config.get_settings", lambda: runtime.settings)
    client, constructor = Mock(), Mock()
    constructor.return_value = client
    monkeypatch.setattr(codex_client, "CodexClient", constructor)
    sample = [
        {"title": "Python HTTP routing", "summary": "Routing maps request paths to handlers."}
    ]
    if enabled:
        assert worker.assess_sample(sample) is client.complete.return_value
        assert client.operation == "source_discovery_quality"
        prompt, schema = client.complete.call_args.args
        assert json.loads(prompt[prompt.index('[{"index"') :]) == [{"index": 0, **sample[0]}]
        assert schema["title"] == "Quality"
        assert client.complete.call_args.kwargs == {}
    else:
        with pytest.raises(ValueError, match="Enable AI"):
            worker.assess_sample(sample)
        constructor.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1])
def test_discovery_rejects_invalid_budget_before_reading_or_opening_redis(runtime, limit):
    with pytest.raises(ValueError, match="positive"):
        worker.dispatch_discovery(runtime.factory, limit=limit)
    runtime.read.execute.assert_not_called()
    assert runtime.queues == []


def test_empty_discovery_cycle_has_no_write_transaction_or_broker_connection(runtime):
    runtime.read.execute.return_value = []
    assert worker.dispatch_discovery(runtime.factory) == 0
    runtime.write.scalars.assert_not_called()
    assert runtime.queues == []


def test_discovery_batch_reuses_each_queue_connection_and_rechecks_authoritative_filters(runtime):
    from sqlalchemy.dialects import postgresql

    jobs = [discovered_job(), discovered_job(), discovered_job("assess")]
    runtime.read.execute.return_value = [(job.id, job.stage) for job in jobs]
    runtime.write.scalars.return_value.all.return_value = jobs
    assert worker.dispatch_discovery(runtime.factory) == 3
    assert [name for name, _ in runtime.queues] == ["source-discovery", "source-analysis"]
    assert [queue.enqueue.call_count for _, queue in runtime.queues] == [2, 1]
    assert all(job.dispatched_at == NOW for job in jobs)
    for _, queue in runtime.queues:
        queue.connection.close.assert_called_once()
    statement = runtime.write.scalars.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "SKIP LOCKED" in sql
    assert "(source_discovery_jobs.id, source_discovery_jobs.stage) IN" in sql
    assert "source_discovery_jobs.result" in sql
    assert "source_discovery_jobs.dispatched_at IS NULL" in sql
    assert "source_discovery_jobs.lease_until <" in sql


def test_discovery_lock_rechecks_the_stages_used_to_prepare_queue_resources(runtime):
    from sqlalchemy.dialects import postgresql

    job = discovered_job()
    runtime.read.execute.return_value = [(job.id, "discover")]
    runtime.write.scalars.return_value.all.return_value = []
    assert worker.dispatch_discovery(runtime.factory) == 0
    statement = runtime.write.scalars.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "(source_discovery_jobs.id, source_discovery_jobs.stage) IN" in str(compiled)
    assert [(job.id, "discover")] in compiled.params.values()
    assert [name for name, _ in runtime.queues] == ["source-discovery"]
    runtime.queues[0][1].enqueue.assert_not_called()
    runtime.queues[0][1].connection.close.assert_called_once()

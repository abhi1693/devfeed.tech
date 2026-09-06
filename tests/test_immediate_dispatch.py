import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import dispatch, scheduler
from devfeed_cli import commands
from devfeed_cli.main import run
from devfeed_core import services
from devfeed_core.models import IngestionJob
from devfeed_core.schemas import JobOut
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)


def queued_job(**changes):
    return IngestionJob(
        **{
            "id": uuid.uuid4(),
            "source_id": uuid.uuid4(),
            "status": "queued",
            "attempts": 1,
            "created_at": NOW - timedelta(minutes=1),
            "available_at": NOW + timedelta(minutes=2),
            "dispatched_at": NOW - timedelta(seconds=5),
            "entries_seen": 0,
            "entries_skipped": 0,
            "articles_created": 0,
            "error": "Previous attempt failed",
            **changes,
        }
    )


def test_prepare_overrides_delays_but_preserves_identity_attempts_and_error(monkeypatch):
    job = queued_job()
    statements, flushes = [], []
    monkeypatch.setattr(services, "utcnow", lambda: NOW)

    def scalar(statement):
        statements.append(statement)
        return job

    session = SimpleNamespace(
        scalar=scalar,
        get=lambda *_: SimpleNamespace(enabled=True, approval_status="approved"),
        flush=lambda: flushes.append(True),
    )
    assert services.prepare_immediate_dispatch(session, job.id) is job
    assert job.available_at == NOW and job.dispatched_at is None
    assert job.status == "queued" and job.attempts == 1
    assert job.error == "Previous attempt failed"
    assert job.lease_token is None and job.lease_until is None
    assert flushes == [True]
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql and "ingestion_jobs.id =" in sql


@pytest.mark.parametrize("status", ["running", "succeeded", "failed"])
def test_prepare_never_changes_a_nonqueued_job(status):
    token = uuid.uuid4()
    job = queued_job(status=status, lease_token=token, lease_until=NOW + timedelta(minutes=5))
    before = (job.available_at, job.dispatched_at, job.attempts, job.lease_token, job.lease_until)
    session = SimpleNamespace(
        scalar=lambda _: job,
        get=lambda *_: pytest.fail("Read source for a rejected job"),
        flush=lambda: pytest.fail("Flushed a rejected job"),
    )
    with pytest.raises(services.OperationConflict, match="Only queued jobs"):
        services.prepare_immediate_dispatch(session, job.id)
    assert (
        job.available_at,
        job.dispatched_at,
        job.attempts,
        job.lease_token,
        job.lease_until,
    ) == before
    assert job.status == status


def test_prepare_rejects_unknown_job_and_disabled_source():
    with pytest.raises(services.RecordNotFound, match="Job not found"):
        services.prepare_immediate_dispatch(SimpleNamespace(scalar=lambda _: None), uuid.uuid4())
    job = queued_job()
    before = (job.available_at, job.dispatched_at)
    session = SimpleNamespace(
        scalar=lambda _: job,
        get=lambda *_: SimpleNamespace(enabled=False, approval_status="approved"),
    )
    with pytest.raises(services.OperationConflict, match="Enable the source"):
        services.prepare_immediate_dispatch(session, job.id)
    assert (job.available_at, job.dispatched_at) == before


@pytest.mark.parametrize("targeted", [False, True])
def test_dispatch_sql_only_selects_target_job_and_preserves_scheduler_lock_behavior(targeted):
    job = queued_job(available_at=NOW, dispatched_at=None)
    statements, calls, events = [], [], []

    def scalar(statement):
        statements.append(statement)
        return job

    @contextmanager
    def begin():
        yield SimpleNamespace(scalar=scalar)
        events.append("commit")

    def enqueue(*args, **kwargs):
        assert job.dispatched_at is None
        calls.append((args, kwargs))
        events.append("publish")
        return SimpleNamespace(id="rq-test-job")

    count = scheduler.dispatch_jobs(
        SimpleNamespace(begin=begin),
        SimpleNamespace(enqueue=enqueue),
        1,
        NOW,
        job_id=job.id if targeted else None,
    )
    assert count == 1 and job.dispatched_at == NOW
    compiled = statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert ("SKIP LOCKED" in sql) is not targeted
    assert ("ingestion_jobs.id =" in sql) is targeted
    if targeted:
        assert job.id in compiled.params.values()
    assert calls[0][0] == ("devfeed_aggregator.tasks.ingest", str(job.id))
    assert events == ["publish", "commit"]


def test_no_eligible_target_does_not_publish_another_job():
    @contextmanager
    def begin():
        yield SimpleNamespace(scalar=lambda _: None)

    queue = SimpleNamespace(enqueue=lambda *_: pytest.fail("Published unrelated job"))
    assert (
        scheduler.dispatch_jobs(SimpleNamespace(begin=begin), queue, 1, NOW, job_id=uuid.uuid4())
        == 0
    )


@pytest.fixture
def immediate_environment(monkeypatch):
    job = queued_job()
    events = []
    session = SimpleNamespace(
        scalar=lambda _: job,
        get=lambda model, _: (
            job
            if model is IngestionJob
            else SimpleNamespace(enabled=True, approval_status="approved")
        ),
        flush=lambda: None,
    )

    @contextmanager
    def begin():
        events.append("begin")
        yield session
        events.append("commit")

    @contextmanager
    def read():
        yield session

    class Factory:
        def begin(self):
            return begin()

        def __call__(self):
            return read()

    factory = Factory()
    queue = SimpleNamespace(connection=SimpleNamespace(close=lambda: events.append("close")))
    monkeypatch.setattr(dispatch, "session_factory", lambda: factory)
    monkeypatch.setattr(dispatch, "get_queue", lambda: queue)
    monkeypatch.setattr(services, "utcnow", lambda: NOW)
    monkeypatch.setattr(dispatch, "utcnow", lambda: NOW)
    return job, factory, queue, events


def test_direct_dispatch_commits_override_before_broker_call(immediate_environment, monkeypatch):
    job, factory, queue, events = immediate_environment

    def publish(actual_factory, actual_queue, batch, now, *, job_id):
        assert events == ["begin", "commit"]
        assert (actual_factory, actual_queue, batch, now, job_id) == (
            factory,
            queue,
            1,
            NOW,
            job.id,
        )
        assert job.available_at == NOW and job.dispatched_at is None
        events.append("publish")
        job.dispatched_at = NOW
        return 1

    monkeypatch.setattr(dispatch, "dispatch_jobs", publish)
    result = dispatch.dispatch_now(job.id)
    assert result["id"] == str(job.id)
    assert datetime.fromisoformat(result["dispatched_at"]) == NOW
    assert events == ["begin", "commit", "publish", "close"]


def test_broker_failure_leaves_durable_override_ready_for_scheduler(
    immediate_environment, monkeypatch
):
    job, _, _, events = immediate_environment

    def unavailable(*args, **kwargs):
        assert events == ["begin", "commit"]
        raise RedisConnectionError("Unavailable")

    monkeypatch.setattr(dispatch, "dispatch_jobs", unavailable)
    with pytest.raises(RedisConnectionError):
        dispatch.dispatch_now(job.id)
    assert job.available_at == NOW and job.dispatched_at is None
    assert events == ["begin", "commit", "close"]


def test_worker_winning_race_is_not_interrupted(immediate_environment, monkeypatch):
    job, _, _, _ = immediate_environment
    token = uuid.uuid4()

    def already_claimed(*args, **kwargs):
        job.status = "running"
        job.lease_token = token
        return 0

    monkeypatch.setattr(dispatch, "dispatch_jobs", already_claimed)
    result = dispatch.dispatch_now(job.id)
    assert result["status"] == "running" and job.lease_token == token


@pytest.mark.parametrize("force", [False, True])
def test_source_fetch_force_dispatches_only_after_releasing_source_transaction(
    monkeypatch, capsys, force
):
    job = queued_job()
    events = []

    @contextmanager
    def begin():
        yield None
        events.append("commit")

    def fetch(session, source_id):
        assert source_id == job.source_id
        return job

    def publish(job_id):
        assert events == ["commit"] and job_id == job.id
        events.append("publish")
        return JobOut.model_validate(job).model_dump(mode="json")

    monkeypatch.setattr(commands, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(services, "fetch_source", fetch)
    monkeypatch.setattr(dispatch, "dispatch_now", publish)
    flags = ["--force"] if force else []
    assert run(["sources", "fetch", str(job.source_id), *flags]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == str(job.id)
    assert events == (["commit", "publish"] if force else ["commit"])


def test_jobs_dispatch_command_is_targeted_and_does_not_start_services(monkeypatch, capsys):
    job_id = uuid.uuid4()
    calls = []
    monkeypatch.setattr(
        dispatch,
        "dispatch_now",
        lambda identifier: calls.append(identifier) or {"id": str(identifier)},
    )
    monkeypatch.setattr(scheduler, "tick", lambda: pytest.fail("Ran scheduler tick"))
    monkeypatch.setattr(commands.worker, "run", lambda **_: pytest.fail("Started worker"))
    assert run(["jobs", "dispatch", str(job_id)]) == 0
    assert calls == [job_id]
    assert json.loads(capsys.readouterr().out)["id"] == str(job_id)

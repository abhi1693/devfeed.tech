"""Network faults through owned proxies, using real application clients and state."""

import importlib
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_core.cache import close_cache
from devfeed_core.cache_events import AppSession
from devfeed_core.config import get_settings
from devfeed_core.db import create_database_engine
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.jobs import request_ingestion
from devfeed_core.models import IngestionJob, Source, utcnow
from devfeed_core.redis import create_redis
from fastapi.testclient import TestClient
from redis.connection import Connection
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from scripts.ci.failure_services import FaultProxy

pytestmark = [pytest.mark.integration, pytest.mark.failure_recovery]


@pytest.fixture
def faults():
    if os.environ.get("DEVFEED_TEST_FAILURE_RECOVERY") != "1":
        pytest.skip("Run through python -m scripts.ci.failure_recovery for owned services")
    proxy = FaultProxy(
        os.environ["DEVFEED_TEST_TOXIPROXY_URL"],
        os.environ["DEVFEED_TEST_TOXIPROXY_CONTAINER"],
        os.environ["DEVFEED_TEST_FAILURE_OWNER"],
    )
    try:
        proxy.verify_targets(
            os.environ["DEVFEED_TEST_DATABASE_URL"], os.environ["DEVFEED_TEST_REDIS_URL"]
        )
    except Exception:
        proxy.close()
        raise
    try:
        yield proxy
    finally:
        proxy.restore()
        proxy.close()


@pytest.fixture
def runtime(faults, database, monkeypatch, request):
    settings = get_settings().model_copy(
        update={
            "database_pool_enabled": getattr(request, "param", False),
            "database_pool_size": 1,
            "database_max_overflow": 0,
        }
    )
    engine = create_database_engine(settings)
    monkeypatch.setattr("devfeed_core.db.get_engine", lambda: engine)
    factory = sessionmaker(engine, class_=AppSession, expire_on_commit=False)
    redis = create_redis(settings)
    clients = {}
    close_cache()
    try:
        with factory.begin() as session:
            source = Source(
                name="Durable source",
                slug="durable-source",
                feed_url="https://publisher.example/rss",
                source_type="publisher",
                approval_status="approved",
                description="Committed before failure",
            )
            session.add(source)
            session.flush()
            job = request_ingestion(session, source)
            source_id, job_id = source.id, job.id
        for service in ("devfeed_api", "devfeed_admin_api", "devfeed_user_api"):
            main = importlib.import_module(service + ".main")
            monkeypatch.setattr(main, "get_redis", lambda: redis)
            app = main.create_app()
            clients[service] = TestClient(app)
            assert clients[service].get("/health/ready").status_code == 200
        yield SimpleNamespace(
            engine=engine,
            factory=factory,
            redis=redis,
            clients=clients,
            source_id=source_id,
            job_id=job_id,
            path=f"/v1/sources/{source_id}",
        )
    finally:
        for client in clients.values():
            client.close()
        close_cache()
        redis.close()
        engine.dispose()


def duration(label, started, maximum):
    elapsed = time.monotonic() - started
    print(f"{label}: {elapsed:.3f}s (budget {maximum}s)")
    assert elapsed < maximum
    return elapsed


def committed(runtime):
    with runtime.factory() as session:
        source = session.get(Source, runtime.source_id)
        job = session.get(IngestionJob, runtime.job_id)
        assert source.description == "Committed before failure"
        assert job.status == "queued" and job.attempts == 0
        assert job.dispatched_at is None


@pytest.mark.parametrize("runtime", [False, True], indirect=True, ids=["null-pool", "queue-pool"])
def test_postgres_latency_preserves_reads(runtime, faults):
    with faults.fault("postgres", "latency", milliseconds=100):
        started = time.monotonic()
        committed(runtime)
        assert duration("PostgreSQL latency", started, 3) >= 0.08


@pytest.mark.parametrize("runtime", [False, True], indirect=True, ids=["null-pool", "queue-pool"])
@pytest.mark.parametrize("fault", ["disconnect", "stall"])
def test_postgres_fault_returns_useful_errors_and_recovers(runtime, faults, fault):
    with faults.fault("postgres", fault):
        for service, client in runtime.clients.items():
            started = time.monotonic()
            response = client.get("/health/ready")
            assert response.status_code == 503 and response.json() == {"status": "unavailable"}
            duration(f"{service} PostgreSQL {fault}", started, 7)
            assert client.get("/health/live").status_code == 200
        response = runtime.clients["devfeed_api"].get(runtime.path)
        assert response.status_code == 503 and "Database unavailable" in response.json()["detail"]
        assert response.headers["cache-control"] == "no-store"
        assert "postgresql" not in response.text and "127.0.0.1" not in response.text
    started = time.monotonic()
    for client in runtime.clients.values():
        assert client.get("/health/ready").status_code == 200
    committed(runtime)
    duration("Same API clients and pools recovered", started, 5)


@pytest.mark.parametrize("runtime", [False, True], indirect=True, ids=["null-pool", "queue-pool"])
def test_interrupted_transaction_preserves_committed_state(runtime, faults):
    started_write = threading.Event()

    def write():
        with runtime.engine.begin() as connection:
            connection.execute(
                text("UPDATE sources SET description = 'Uncommitted write' WHERE id = :id"),
                {"id": runtime.source_id},
            )
            started_write.set()
            connection.execute(text("SELECT pg_sleep(10)"))

    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(write)
        assert started_write.wait(5)
        with faults.fault("postgres", "disconnect"):
            started = time.monotonic()
            with pytest.raises(DBAPIError) as error:
                active.result(timeout=8)
            assert error.value.connection_invalidated
            duration("Interrupted PostgreSQL transaction", started, 8)
    committed(runtime)
    with runtime.factory.begin() as session:
        session.get(Source, runtime.source_id).description = "Explicit recovery write"
    with runtime.factory() as session:
        assert session.get(Source, runtime.source_id).description == "Explicit recovery write"
        assert session.scalar(text("SHOW statement_timeout")) == "30s"


def test_redis_latency_preserves_state(runtime, faults):
    runtime.redis.set("durable-state", "retained")
    with faults.fault("redis", "latency", milliseconds=100):
        started = time.monotonic()
        assert runtime.redis.get("durable-state") == b"retained"
        assert duration("Redis latency", started, 2) >= 0.08


def test_redis_connection_retries_are_bounded(runtime, faults, monkeypatch):
    client = create_redis(get_settings())
    attempts = []
    connect = Connection._connect

    def counted(connection):
        attempts.append(time.monotonic())
        return connect(connection)

    monkeypatch.setattr(Connection, "_connect", counted)
    try:
        with faults.fault("redis", "disconnect"):
            started = time.monotonic()
            with pytest.raises(RedisError):
                client.ping()
            assert len(attempts) == 4  # Initial TCP dial plus the three production retries.
            duration("Default Redis retry budget", started, 35)
        assert client.ping() is True
        committed(runtime)
    finally:
        client.close()


@pytest.mark.parametrize("fault", ["disconnect", "stall"])
def test_redis_readiness_recovers(runtime, faults, fault):
    runtime.redis.set("durable-state", "retained")
    client = runtime.clients["devfeed_user_api"]
    with faults.fault("redis", fault):
        started = time.monotonic()
        response = client.get("/health/ready")
        assert response.status_code == 503 and response.json() == {"status": "unavailable"}
        duration(f"Redis readiness {fault}", started, 45)
        assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert runtime.redis.get("durable-state") == b"retained"
    committed(runtime)


@pytest.mark.parametrize("fault", ["disconnect", "latency"])
def test_cache_outage_preserves_writes_and_recovers(runtime, faults, fault, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    monkeypatch.setattr(get_settings(), "cache_metadata_ttl_seconds", 1)
    client = runtime.clients["devfeed_api"]
    assert client.get(runtime.path).headers["x-cache"] == "MISS"
    assert client.get(runtime.path).headers["x-cache"] == "HIT"
    with faults.fault("redis", fault, milliseconds=600):
        started = time.monotonic()
        response = client.get(runtime.path)
        assert response.status_code == 200 and response.headers["x-cache"] == "BYPASS"
        duration(f"Public cache fallback {fault}", started, 2)
        started = time.monotonic()
        with runtime.factory.begin() as session:
            session.get(Source, runtime.source_id).description = "Committed during Redis failure"
        duration("Commit despite failed cache invalidation", started, 2)
        assert client.get(runtime.path).json()["description"] == "Committed during Redis failure"
    # Let the real breaker and short, configured TTL expire; do not reset clients.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(runtime.path)
        if response.headers["x-cache"] == "MISS":
            break
        time.sleep(0.05)
    assert response.headers["x-cache"] == "MISS"
    assert response.json()["description"] == "Committed during Redis failure"
    assert client.get(runtime.path).headers["x-cache"] == "HIT"


def test_failed_redis_dispatch_preserves_durable_job(runtime, faults):
    queue = get_queue("ingestion")
    try:
        with faults.fault("redis", "disconnect"):
            started = time.monotonic()
            with pytest.raises(RedisError):
                dispatch_jobs(runtime.factory, queue, 1, utcnow(), job_id=runtime.job_id)
            duration("Failed durable dispatch", started, 35)
        committed(runtime)
        assert dispatch_jobs(runtime.factory, queue, 1, utcnow(), job_id=runtime.job_id) == 1
        assert dispatch_jobs(runtime.factory, queue, 1, utcnow(), job_id=runtime.job_id) == 0
        assert queue.count == 1
        with runtime.factory() as session:
            job = session.get(IngestionJob, runtime.job_id)
            assert job.status == "queued" and job.attempts == 0 and job.dispatched_at is not None
    finally:
        queue.connection.close()


def test_lost_database_commit_does_not_duplicate_rq_delivery(runtime, faults, monkeypatch):
    queue = get_queue("ingestion")
    enqueue = queue.enqueue

    def publish_then_disconnect(*args, **kwargs):
        delivery = enqueue(*args, **kwargs)
        faults.enabled("postgres", False)
        return delivery

    try:
        monkeypatch.setattr(queue, "enqueue", publish_then_disconnect)
        try:
            with pytest.raises(DBAPIError):
                dispatch_jobs(runtime.factory, queue, 1, utcnow(), job_id=runtime.job_id)
        finally:
            faults.restore()
        monkeypatch.setattr(queue, "enqueue", enqueue)
        committed(runtime)
        assert queue.count == 1
        assert dispatch_jobs(runtime.factory, queue, 1, utcnow(), job_id=runtime.job_id) == 1
        assert queue.count == 1
        assert queue.get_jobs()[0].args == [str(runtime.job_id)]  # RQ uses JSONSerializer.
    finally:
        queue.connection.close()

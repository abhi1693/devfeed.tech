import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest
from devfeed_core import telemetry
from devfeed_core.config import get_settings
from devfeed_core.database_telemetry import instrument_engine
from devfeed_core.logging import JsonFormatter
from devfeed_core.metrics import Metrics
from devfeed_core.observability_exporter import SnapshotCollector, gauge
from devfeed_http.telemetry import TelemetryMiddleware
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


@pytest.fixture
def observed_runtime(monkeypatch):
    runtime = telemetry.Runtime("api", get_settings())
    exporter = InMemorySpanExporter()
    runtime.provider = TracerProvider(shutdown_on_exit=False)
    runtime.provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_runtime", runtime)
    yield runtime, exporter
    runtime.close()


def test_metrics_listener_only_serves_separate_metrics_path():
    metrics = Metrics("api", "test")
    metrics.listen("127.0.0.1", 0)
    try:
        port = metrics.server.server_port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as response:
            assert b"devfeed_build_info" in response.read()
        for path in ("/", "/metrics?debug=true", "/version", "/health/ready"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
            assert error.value.code == 404
    finally:
        metrics.close()


def test_http_cardinality_errors_and_private_attributes(observed_runtime):
    runtime, exporter = observed_runtime
    app = FastAPI()
    app.add_middleware(TelemetryMiddleware)

    @app.get("/articles/{identifier}")
    def article(identifier: str):
        return {"ok": True}

    @app.get("/broken")
    def broken():
        raise RuntimeError("secret-user-content-and-token")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            for i in range(40):
                assert (
                    await client.get(f"/articles/private-{i}?access_token=secret-{i}")
                ).status_code == 200
                assert (await client.get(f"/unknown-private-{i}")).status_code == 404
            assert (await client.get("/metrics")).status_code == 404
            assert (await client.get("/broken")).status_code == 500

    asyncio.run(exercise())
    exposition = generate_latest(runtime.metrics.registry).decode()
    assert 'route="/articles/{identifier}"' in exposition
    assert 'route="unmatched"' in exposition
    assert 'route="/broken",service="api",status="500"' in exposition
    assert runtime.metrics.inflight.labels("api")._value.get() == 0
    spans = exporter.get_finished_spans()
    payload = json.dumps(
        [
            {"name": s.name, "attributes": dict(s.attributes), "events": list(s.events)}
            for s in spans
        ],
        default=str,
    )
    for secret in ("private-", "access_token", "secret-user", "secret-"):
        assert secret not in exposition
        assert secret not in payload


def test_database_spans_and_logs_correlate_without_sql_or_bind_values(observed_runtime):
    runtime, exporter = observed_runtime
    engine = create_engine("sqlite://")
    instrument_engine(engine)
    with telemetry.span("safe-operation"):
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT :private_value"), {"private_value": "private-record"}
                )
                == "private-record"
            )
            with pytest.raises(OperationalError):
                connection.execute(text("SELECT private_column FROM private_table"))
        record = logging.LogRecord(
            "devfeed_core.test", logging.INFO, "test.py", 1, "query_completed", (), None
        )
        payload = JsonFormatter("api").payload(record)
    assert len(payload["trace_id"]) == 32
    assert len(payload["span_id"]) == 16
    serialized = str([dict(s.attributes) for s in exporter.get_finished_spans()])
    assert "private_" not in serialized
    assert "private-record" not in serialized
    assert runtime.metrics.db_errors.labels("api", "select")._value.get() == 1
    assert runtime.metrics.pool_connections.labels("api")._value.get() == 0
    engine.dispose()


def test_fork_cannot_reuse_parent_runtime(observed_runtime, monkeypatch):
    runtime, _ = observed_runtime
    monkeypatch.setattr(telemetry.os, "getpid", lambda: runtime.pid + 1)
    assert telemetry.current() is None


def test_tracing_propagates_only_valid_w3c_parent(observed_runtime):
    with telemetry.span("parent"):
        carrier = telemetry.inject_context()
    assert set(carrier) == {"traceparent"}
    context = telemetry.extract_context(
        {**carrier, "baggage": "email=private", "tracestate": "user=private"}
    )
    with telemetry.span("child", context=context):
        assert (
            telemetry.inject_context()["traceparent"].split("-")[1]
            == carrier["traceparent"].split("-")[1]
        )
    assert not telemetry.inject_context()


def test_collector_failure_retains_snapshot_and_marks_it_stale():
    collector = SnapshotCollector()
    registry = CollectorRegistry()
    registry.register(collector)
    collector.refresh("database", lambda: [gauge("jobs", "jobs", ["status"], [(("queued",), 42)])])
    timestamp = collector.last_success["database"]

    def fail():
        raise OSError("database credential must not be logged")

    collector.refresh("database", fail)
    payload = generate_latest(registry).decode()
    assert 'devfeed_jobs{status="queued"} 42.0' in payload
    assert 'devfeed_exporter_dependency_up{dependency="database"} 0.0' in payload
    assert collector.last_success["database"] == timestamp
    assert "credential" not in payload


def test_shutdown_is_bounded_when_a_collector_stalls():
    stop = threading.Event()
    started = time.monotonic()
    try:
        telemetry.bounded_shutdown(lambda: stop.wait(10), timeout=0.02)
        assert time.monotonic() - started < 0.5
    finally:
        stop.set()


@pytest.mark.integration
def test_exporter_uses_durable_outcomes_and_live_worker_heartbeats(database):
    from datetime import UTC, datetime, timedelta

    from devfeed_core.db import get_engine
    from devfeed_core.models import Article, ArticleAnalysisJob
    from devfeed_core.observability_exporter import database_snapshot, redis_snapshot

    now = datetime.now(UTC)
    with database.begin() as session:
        for status, attempts, finished, available in [
            ("queued", 0, None, now - timedelta(seconds=120)),
            ("queued", 2, None, now + timedelta(seconds=60)),
            ("running", 1, None, now),
            ("succeeded", 3, now - timedelta(seconds=60), now),
            ("failed", 2, now - timedelta(seconds=30), now),
        ]:
            import uuid

            article = Article(
                canonical_url="https://example.com/telemetry-test",
                url_hash=uuid.uuid4().hex,
                title="Telemetry test",
                metadata_source_type="publisher",
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleAnalysisJob(
                    article_id=article.id,
                    status=status,
                    attempts=attempts,
                    created_at=now - timedelta(seconds=300),
                    available_at=available,
                    finished_at=finished,
                    lease_until=now - timedelta(seconds=10) if status == "running" else None,
                    error="private-error-must-not-appear" if status == "failed" else None,
                )
            )
    families = database_snapshot(get_engine(), now, 3000)
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in families
        for sample in family.samples
    }

    def value(name, **labels):
        return samples[("devfeed_" + name, tuple(sorted(labels.items())))]

    assert (
        value("jobs", queue="article-analysis-fresh", kind="article-analysis", status="queued") == 2
    )
    assert value("jobs_due", queue="article-analysis-fresh", kind="article-analysis") == 1
    assert value("jobs_retrying", queue="article-analysis-fresh", kind="article-analysis") == 1
    assert (
        value(
            "jobs_oldest_due_age_seconds", queue="article-analysis-fresh", kind="article-analysis"
        )
        == 120
    )
    assert (
        value("jobs_expired_leases", queue="article-analysis-fresh", kind="article-analysis") == 1
    )
    assert (
        value("jobs_completed_window", kind="article-analysis", status="succeeded", window="5m")
        == 1
    )
    assert (
        value("job_retry_attempts_window", kind="article-analysis", status="succeeded", window="5m")
        == 2
    )
    assert value("job_failures_24h", kind="article-analysis", error="other") == 1
    assert "private-error" not in str(samples)
    from redis import Redis

    redis = Redis.from_url(get_settings().redis_url)
    for worker, age in (("live", 10), ("stale", 120)):
        key = "rq:worker:" + worker
        redis.sadd("rq:workers", key)
        redis.hset(
            key,
            mapping={
                "queues": "article-analysis",
                "state": "busy",
                "last_heartbeat": (now - timedelta(seconds=age)).isoformat(),
            },
        )
        redis.expire(key, 300)
    redis.rpush("rq:queue:article-analysis", "delivery-one", "delivery-two")
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in redis_snapshot(redis, now)
        for sample in family.samples
    }
    assert value("queue_workers", queue="article-analysis", state="busy") == 1
    assert value("queue_stale_workers", queue="article-analysis") == 1
    assert value("queue_deliveries", queue="article-analysis") == 2

    redis.close()


@pytest.mark.integration
def test_real_rq_fork_reports_execution_without_treating_it_as_durable_success(
    database, monkeypatch, free_tcp_port
):
    import operator

    from devfeed_aggregator.analysis_worker import AnalysisAwareWorker
    from redis import Redis
    from rq import Queue

    monkeypatch.setenv("DEVFEED_METRICS_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_METRICS_PORT", str(free_tcp_port))
    monkeypatch.setenv("DEVFEED_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("DEVFEED_PYROSCOPE_SERVER", "http://127.0.0.1:1")
    get_settings.cache_clear()
    runtime = telemetry.start_runtime(
        "worker-ingestion", serve_metrics=True, profiling=False, tracing=False
    )
    redis = Redis.from_url(get_settings().redis_url)
    queue = Queue("ingestion", connection=redis)
    job = queue.enqueue(operator.add, 20, 22)
    worker = AnalysisAwareWorker([queue], connection=redis)
    try:
        worker.work(burst=True, with_scheduler=False, logging_level="ERROR")
        job.refresh()
        assert job.is_finished
        assert job.return_value() == 42
        assert runtime.metrics.executions.labels("worker-ingestion", "ingestion")._value.get() == 1
        assert runtime.metrics.worker_busy.labels("worker-ingestion")._value.get() == 0
        assert runtime.profiler is None  # The native profiler ran only in the child.
    finally:
        redis.close()
        telemetry.stop_runtime(runtime)

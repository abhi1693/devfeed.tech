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
from devfeed_core.metrics import MetricServer
from devfeed_http.telemetry import fastapi_telemetry
from fastapi import FastAPI
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import generate_latest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from test_cache import cached_client as cached_client
from test_cache import response_cache as response_cache


def test_telemetry_resource_has_pod_instance_id(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "devfeed-worker-notifications-abc123")
    runtime = telemetry.Runtime("worker-notifications", get_settings())

    assert runtime.resource_attributes() == {
        "service.name": "devfeed-worker-notifications",
        "service.instance.id": "devfeed-worker-notifications-abc123",
        "service.version": telemetry.__version__,
        "deployment.environment.name": runtime.settings.telemetry_environment,
    }


@pytest.fixture
def observed_runtime(monkeypatch):
    runtime = telemetry.Runtime("api", get_settings())
    exporter = InMemorySpanExporter()
    runtime.provider = TracerProvider(shutdown_on_exit=False)
    runtime.provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime.metric_reader = InMemoryMetricReader()
    runtime.start_metrics(
        readers=[
            runtime.metric_reader,
            PrometheusMetricReader(
                registry=runtime.metric_server.registry,
                scope_info_enabled=False,
                resource_attribute_filter=lambda key: key == "service.name",
            ),
        ]
    )
    monkeypatch.setattr(telemetry, "_runtime", runtime)
    yield runtime, exporter
    runtime.close()


def test_metrics_listener_only_serves_separate_metrics_path():
    metrics = MetricServer()
    metrics.listen("127.0.0.1", 0)
    try:
        port = metrics.server.server_port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics") as response:
            assert response.headers["Content-Type"].startswith("text/plain")
        for path in ("/", "/metrics?debug=true", "/version", "/health/ready"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
            assert error.value.code == 404
    finally:
        metrics.close()


def test_http_cardinality_errors_and_private_attributes(observed_runtime):
    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())

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
    exposition = generate_latest(runtime.metric_server.registry).decode()
    assert 'http_route="/articles/{identifier}"' in exposition
    assert 'http_response_status_code="404"' in exposition
    assert 'http_route="/broken"' in exposition
    assert 'http_response_status_code="500"' in exposition
    assert "http_server_active_requests" in exposition
    assert "devfeed_http_request_duration_seconds_count" not in exposition
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
    assert (
        next(
            point
            for point in otel_points(runtime, "db.client.operation.duration")
            if "error.type" in point.attributes
        ).count
        == 1
    )
    assert otel_points(runtime, "db.client.connection.count")[0].value == 0
    engine.dispose()


def test_fork_cannot_reuse_parent_runtime(observed_runtime, monkeypatch):
    runtime, _ = observed_runtime
    monkeypatch.setattr(telemetry.os, "getpid", lambda: runtime.pid + 1)
    assert telemetry.current() is None


def test_pool_records_checkout_timeout_and_connection_hold(observed_runtime):
    from devfeed_core.database_telemetry import ObservedQueuePool
    from sqlalchemy.exc import TimeoutError

    runtime, _ = observed_runtime
    engine = create_engine(
        "sqlite://", poolclass=ObservedQueuePool, pool_size=1, max_overflow=0, pool_timeout=0.01
    )
    instrument_engine(engine)
    try:
        with engine.connect(), pytest.raises(TimeoutError):
            engine.connect()
        points = otel_points(runtime, "db.client.connection.wait_time")
        assert {point.attributes["outcome"] for point in points} == {"ok", "timeout"}
        assert all(point.count == 1 for point in points)
        assert otel_points(runtime, "db.client.connection.count")[0].value == 0
    finally:
        engine.dispose()


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


def test_shutdown_is_bounded_when_a_collector_stalls():
    stop = threading.Event()
    started = time.monotonic()
    try:
        telemetry.bounded_shutdown(lambda: stop.wait(10), timeout=0.02)
        assert time.monotonic() - started < 0.5
    finally:
        stop.set()


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
        payload = generate_latest(runtime.metric_server.registry).decode()
        assert "devfeed_worker_executions_total" in payload
        assert "devfeed_worker_active" in payload
        assert runtime.profiler is None  # The native profiler ran only in the child.
    finally:
        redis.close()
        telemetry.stop_runtime(runtime)


@pytest.mark.parametrize("service", ["api", "admin-api", "user-api"])
def test_private_api_factories_record_http_metrics(observed_runtime, service):
    from importlib import import_module

    runtime, _ = observed_runtime
    runtime.service = service
    app = import_module(f"devfeed_{service.replace('-', '_')}.main").create_app()

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/unknown-private-id?token=secret")).status_code == 404
            assert (await client.get("/health/live")).status_code == 200

    asyncio.run(exercise())
    exposition = generate_latest(runtime.metric_server.registry).decode()
    assert 'http_response_status_code="404"' in exposition
    assert "http_server_request_duration_seconds_count" in exposition
    assert "/health/live" not in exposition
    assert "unknown-private-id" not in exposition
    assert "token=secret" not in exposition


def test_null_pool_records_connection_creation_failure_and_release(observed_runtime):
    from devfeed_core.database_telemetry import ObservedNullPool
    from sqlalchemy.exc import OperationalError

    runtime, _ = observed_runtime
    engine = create_engine("sqlite://", poolclass=ObservedNullPool)
    instrument_engine(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 1")) == 1
        assert otel_points(runtime, "db.client.connection.count")[0].value == 1
    assert (
        not otel_points(runtime, "db.client.connection.count")
        or otel_points(runtime, "db.client.connection.count")[0].value == 0
    )
    engine.dispose()

    def fail():
        raise OperationalError("connect", None, Exception("unavailable"))

    engine = create_engine("sqlite://", poolclass=ObservedNullPool, creator=fail)
    with pytest.raises(OperationalError):
        engine.connect()
    points = otel_points(runtime, "db.client.connection.wait_time")
    assert {point.attributes["outcome"] for point in points} == {"ok", "error"}
    assert all(point.count == 1 for point in points)
    engine.dispose()


def test_admission_rejection_has_a_separate_metric(observed_runtime):
    from devfeed_http.admission import AdmissionMiddleware

    runtime, _ = observed_runtime

    async def unused(*args):
        return {}

    middleware = AdmissionMiddleware(unused, requests=1, streams=1)
    middleware.active["request"] = 1
    asyncio.run(middleware({"type": "http", "path": "/busy"}, unused, unused))
    assert otel_points(runtime, "devfeed.admission.rejections")[0].value == 1
    assert (
        not otel_points(runtime, "db.client.connection.count")
        or otel_points(runtime, "db.client.connection.count")[0].value == 0
    )


def otel_points(runtime, name):
    data = runtime.metric_reader.get_metrics_data()
    return [
        point
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def test_native_request_has_one_server_span_and_safe_operation_children(observed_runtime):
    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"

    @app.get("/records/{identifier}")
    def record(identifier: str):
        with telemetry.dependency_call("typesense", "request"):
            return {"ok": True}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            await client.get(
                "/records/private-user?q=private-query&token=private-secret",
                headers={
                    "traceparent": parent,
                    "tracestate": "tenant=private-tenant",
                    "baggage": "email=private-email",
                    "Authorization": "Bearer private-token",
                },
            )

    asyncio.run(exercise())
    spans = exporter.get_finished_spans()
    servers = [item for item in spans if item.kind == telemetry.trace.SpanKind.SERVER]
    assert len(servers) == 1
    server = servers[0]
    assert server.name == "GET /records/{identifier}"
    assert server.context.trace_id == int(parent.split("-")[1], 16)
    assert not server.context.trace_state
    assert any(item.name == "fastapi.endpoint" for item in spans)
    assert any(item.name == "typesense.request" for item in spans)
    assert all(item.context.trace_id == server.context.trace_id for item in spans)
    assert "private-" not in str([(item.attributes, item.events, item.context) for item in spans])
    points = otel_points(runtime, "http.server.request.duration")
    assert len(points) == 1 and points[0].count == 1
    assert points[0].attributes["http.route"] == "/records/{identifier}"
    assert points[0].attributes["http.response.status_code"] == 200
    assert otel_points(runtime, "devfeed.dependency.duration")[0].count == 1


def test_native_response_timing_excludes_background_work(observed_runtime):
    runtime, exporter = observed_runtime
    from fastapi import BackgroundTasks

    app = FastAPI(telemetry=fastapi_telemetry())
    durations_at_background_start = []

    def background():
        durations_at_background_start.extend(otel_points(runtime, "http.server.request.duration"))
        raise RuntimeError("private-background-message")

    @app.get("/background")
    def endpoint(tasks: BackgroundTasks):
        tasks.add_task(background)
        return {"ok": True}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            assert (await client.get("/background")).status_code == 200

    asyncio.run(exercise())
    assert len(durations_at_background_start) == 1
    assert durations_at_background_start[0].count == 1
    spans = exporter.get_finished_spans()
    server = next(item for item in spans if item.kind == telemetry.trace.SpanKind.SERVER)
    background_span = next(item for item in spans if item.name == "fastapi.background_task")
    assert server.end_time <= background_span.start_time
    assert background_span.status.status_code == telemetry.trace.StatusCode.ERROR
    assert "private-background" not in str(
        [(item.attributes, item.events, item.status) for item in spans]
    )


def test_stream_latency_is_distinct_from_interactive_requests(observed_runtime):
    runtime, _ = observed_runtime
    from fastapi.responses import StreamingResponse

    app = FastAPI(telemetry=fastapi_telemetry())

    @app.get("/v1/user/notifications/chimely/v1/inbox/{path:path}")
    def endpoint(path: str):
        return StreamingResponse(iter([b": connected\n\n", b": heartbeat\n\n"]))

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            await client.get("/v1/user/notifications/chimely/v1/inbox/stream")
            await client.get("/v1/user/notifications/chimely/v1/inbox/items")

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {point.attributes["devfeed.request.kind"] for point in points} == {"request", "stream"}
    assert sum(point.count for point in points) == 2
    assert all(point.value == 0 for point in otel_points(runtime, "http.server.active_requests"))


def test_native_admission_pressure_includes_rejections_and_releases_slots(observed_runtime):
    runtime, _ = observed_runtime
    from devfeed_http.admission import AdmissionMiddleware

    app = FastAPI(telemetry=fastapi_telemetry())
    app.add_middleware(AdmissionMiddleware, requests=1, streams=1)

    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()

        @app.get("/blocked")
        async def blocked():
            started.set()
            await finish.wait()
            return {"ok": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            request = asyncio.create_task(client.get("/blocked"))
            await started.wait()
            assert (await client.get("/blocked")).status_code == 503
            assert otel_points(runtime, "devfeed.admission.active")[0].value == 1
            finish.set()
            assert (await request).status_code == 200

    asyncio.run(exercise())
    assert otel_points(runtime, "devfeed.admission.active")[0].value == 0
    assert otel_points(runtime, "devfeed.admission.limit")[0].value == 1
    assert otel_points(runtime, "devfeed.admission.rejections")[0].value == 1
    points = otel_points(runtime, "http.server.request.duration")
    assert {point.attributes["http.response.status_code"] for point in points} == {200, 503}


def test_standard_otlp_setting_and_emergency_disable(monkeypatch):
    monkeypatch.delenv("DEVFEED_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    get_settings.cache_clear()
    assert get_settings().otlp_endpoint == "http://127.0.0.1:4318"
    monkeypatch.setenv("DEVFEED_OTLP_ENDPOINT", "http://127.0.0.1:14318")
    get_settings.cache_clear()
    assert get_settings().otlp_endpoint == "http://127.0.0.1:14318"
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert telemetry.start_runtime("api") is None


def test_public_cache_records_final_outcome_once(
    observed_runtime, cached_client, response_cache, monkeypatch
):
    from redis.exceptions import ConnectionError

    runtime, _ = observed_runtime
    client, *_ = cached_client
    assert client.get("/v1/feed").headers["X-Cache"] == "MISS"
    assert client.get("/v1/feed").headers["X-Cache"] == "HIT"
    assert (
        client.get("/v1/feed", headers={"Cache-Control": "no-store"}).headers["X-Cache"] == "BYPASS"
    )
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    assert client.get("/v1/feed").headers["X-Cache"] == "BYPASS"
    monkeypatch.setattr(get_settings(), "cache_enabled", True)

    def fail(*args):
        raise ConnectionError("private-redis-credentials")

    monkeypatch.setattr(response_cache.redis, "get", fail)
    assert client.get("/v1/feed").headers["X-Cache"] == "BYPASS"
    points = otel_points(runtime, "devfeed.cache.reads")
    assert sum(point.value for point in points) == 5
    assert {point.attributes["cache.outcome"] for point in points} == {"hit", "miss", "bypass"}
    assert {point.attributes["cache.bypass_reason"] for point in points} == {
        "none",
        "request_cache_control",
        "disabled",
        "cache_unavailable",
    }
    assert all(point.attributes["cache.name"] == "public_response" for point in points)
    assert "private-" not in str([point.attributes for point in points])


def test_cache_metric_is_shared_and_does_not_label_keys(observed_runtime):
    from unittest.mock import MagicMock

    from devfeed_core.cache import CacheUnavailable, ResponseCache, record_cache_read
    from test_cache import MemoryRedis

    runtime, _ = observed_runtime
    cache = ResponseCache(MemoryRedis(), "test")
    for domain in ["admin-overview", "admin-overview-panels"]:
        lookup = cache.lookup("private-key", domain)
        assert cache.publish(lookup, b'{"ok":true}', 60)
        assert cache.lookup("private-key", domain).body
    record_cache_read("public_profile", "bypass", "disabled")
    pipe = MagicMock()
    pipe.__enter__.return_value = pipe
    pipe.exists.return_value = pipe
    pipe.lrange.return_value = pipe
    pipe.execute.side_effect = [(0, []), (1, [b"one"])]
    cache.redis.pipeline = lambda: pipe
    cache.read_sequence("private-user", 0, 0)
    cache.read_sequence("private-user", 0, 0)
    cache.blocked_until = time.monotonic() + 30
    with pytest.raises(CacheUnavailable):
        cache.read_sequence("private-user", 0, 0)
    record_cache_read("private-key", "private-value", "private-reason")
    points = otel_points(runtime, "devfeed.cache.reads")
    assert {point.attributes["cache.name"] for point in points} == {
        "admin_overview",
        "admin_panels",
        "public_profile",
        "feed_sequence",
        "other",
    }
    assert "private-" not in str([point.attributes for point in points])


@pytest.mark.parametrize("service", ["api", "admin-api", "user-api"])
def test_real_api_lifespan_exports_native_otlp_without_duplicate_spans(service, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from importlib import import_module

    from fastapi.testclient import TestClient
    from google.protobuf.json_format import MessageToJson
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = (
                ExportTraceServiceRequest()
                if self.path == "/v1/traces"
                else ExportMetricsServiceRequest()
            )
            message.ParseFromString(body)
            received.append((self.path, message))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEVFEED_OTLP_ENDPOINT", f"http://127.0.0.1:{collector.server_port}")
    monkeypatch.setenv("DEVFEED_TRACE_SAMPLE_RATIO", "1")
    get_settings.cache_clear()
    app = import_module(f"devfeed_{service.replace('-', '_')}.main").create_app()

    @app.get("/otel-test/{identifier}")
    def endpoint(identifier: str):
        return {"ok": True}

    try:
        with TestClient(app) as client:
            assert client.get("/health/live").status_code == 200
            assert client.get("/otel-test/private-id?token=private-secret").status_code == 200
            assert telemetry.current().provider.force_flush(timeout_millis=2000)
            assert telemetry.current().meter_provider.force_flush(timeout_millis=2000)
        traces = [item for path, item in received if path == "/v1/traces"]
        metric_batches = [item for path, item in received if path == "/v1/metrics"]
        spans = [
            span
            for batch in traces
            for resource in batch.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
        ]
        servers = [span for span in spans if span.kind == 2]
        assert len(servers) == 1
        assert servers[0].name == "GET /otel-test/{identifier}"
        assert any(
            metric.name == "http.server.request.duration"
            for batch in metric_batches
            for resource in batch.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        )
        payload = "".join(MessageToJson(item) for _, item in received)
        assert f"devfeed-{service}" in payload
        assert "private-" not in payload
        assert telemetry.current() is None
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=1)


def test_telemetry_failure_does_not_break_api_or_background_work(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http import metric_exporter, trace_exporter

    def fail(*args, **kwargs):
        raise ValueError("private-export-credential")

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", fail)
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", fail)
    monkeypatch.setenv("DEVFEED_OTLP_ENDPOINT", "http://127.0.0.1:1")
    get_settings.cache_clear()
    runtime = telemetry.start_runtime("api", serve_metrics=False, profiling=False)
    app = FastAPI(telemetry=fastapi_telemetry())

    @app.get("/ok")
    def endpoint():
        with telemetry.background_cycle("test-cycle"):
            return {"ok": True}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/ok")).status_code == 200

    try:
        asyncio.run(exercise())
        assert runtime.provider is not None
    finally:
        telemetry.stop_runtime(runtime)

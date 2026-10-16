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
from devfeed_http.route_policies import RouteRuntimePolicy, install_runtime_policies
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


@pytest.mark.parametrize("service", ["api", "admin-api", "user-api", "partner-api"])
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
            if service != "api":
                assert (await client.get("/version")).status_code == 404

    asyncio.run(exercise())
    exposition = generate_latest(runtime.metric_server.registry).decode()
    assert 'http_response_status_code="404"' in exposition
    assert "http_server_request_duration_seconds_count" in exposition
    assert "/health/live" not in exposition
    assert "unknown-private-id" not in exposition
    assert "token=secret" not in exposition
    errors = [
        point
        for point in otel_points(runtime, "http.server.request.duration")
        if point.attributes["http.response.status_code"] == 404
    ]
    assert sum(point.count for point in errors) == (1 if service == "api" else 2)


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
    from devfeed_http.admission import AdmissionController

    runtime, _ = observed_runtime

    async def unused(*args):
        return {}

    admission = AdmissionController(requests=1, streams=1)
    admission.active["request"] = 1
    asyncio.run(admission.run("request", {"type": "http", "path": "/busy"}, unused, unused, unused))
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

    @app.get(
        "/renamed-events", openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra()
    )
    def events():
        return StreamingResponse(iter([b": connected\n\n", b": heartbeat\n\n"]))

    @app.get("/v1/user/notifications/chimely/v1/inbox/stream")
    def ordinary():
        return {"ok": True}

    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            await client.get("/renamed-events")
            await client.get("/v1/user/notifications/chimely/v1/inbox/stream")

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {point.attributes["devfeed.request.kind"] for point in points} == {"request", "stream"}
    assert {
        point.attributes["http.route"]: point.attributes["devfeed.request.kind"] for point in points
    } == {
        "/renamed-events": "stream",
        "/v1/user/notifications/chimely/v1/inbox/stream": "request",
    }
    assert sum(point.count for point in points) == 2
    assert all(point.value == 0 for point in otel_points(runtime, "http.server.active_requests"))


def test_runtime_telemetry_policy_follows_renamed_and_mounted_routes(observed_runtime):
    from fastapi import APIRouter
    from fastapi.responses import StreamingResponse

    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())
    child = FastAPI(telemetry=fastapi_telemetry())
    router = APIRouter(prefix="/renamed")

    @router.get(
        "/quiet/{identifier}",
        openapi_extra=RouteRuntimePolicy(
            admission="exempt", telemetry_excluded=True, logging_level="debug"
        ).openapi_extra(),
    )
    def quiet(identifier: str):
        return {"ok": True}

    @router.get(
        "/events/{identifier}",
        openapi_extra=RouteRuntimePolicy(admission="stream").openapi_extra(),
    )
    def events(identifier: str):
        return StreamingResponse(iter([b": connected\n\n"]))

    for path in ("/health/live", "/health/ready", "/version"):
        child.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
    child.include_router(router)
    app.mount("/proxy", child)
    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for index in range(5):
                assert (
                    await client.get(f"/proxy/renamed/quiet/private-{index}?token=private-secret")
                ).status_code == 200
                assert (
                    await client.get(f"/proxy/renamed/events/private-{index}?token=private-secret")
                ).status_code == 200
            for path in ("/health/live", "/health/ready", "/version"):
                assert (await client.get("/proxy" + path)).status_code == 200

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {
        point.attributes["http.route"]: (point.attributes["devfeed.request.kind"], point.count)
        for point in points
    } == {
        "/proxy/renamed/events/{identifier}": ("stream", 5),
        "/proxy/health/live": ("request", 1),
        "/proxy/health/ready": ("request", 1),
        "/proxy/version": ("request", 1),
    }
    spans = exporter.get_finished_spans()
    servers = [item for item in spans if item.kind == telemetry.trace.SpanKind.SERVER]
    assert len(servers) == 8  # The mounted app must not create a second server span.
    assert "quiet" not in str([(item.name, item.attributes) for item in spans])
    payload = generate_latest(runtime.metric_server.registry).decode() + str(
        [(item.name, item.attributes, item.events) for item in spans]
    )
    assert "private-" not in payload
    assert "token=" not in payload
    assert all(point.value == 0 for point in otel_points(runtime, "http.server.active_requests"))


@pytest.mark.parametrize("included", [False, True])
def test_dynamic_mount_templates_keep_reader_identifiers_out_of_telemetry(
    observed_runtime, included
):
    from fastapi import APIRouter

    runtime, exporter = observed_runtime
    app, child = FastAPI(telemetry=fastapi_telemetry()), FastAPI(telemetry=fastapi_telemetry())

    @child.get("/records/{identifier}")
    def endpoint(identifier: str):
        return {"ok": True}

    prefix = "/nested" if included else ""
    if included:
        router = APIRouter()
        router.mount("/tenants/{tenant_id}", child)
        app.include_router(router, prefix=prefix)
    else:
        app.mount("/tenants/{tenant_id}", child)
    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            for index in range(5):
                assert (
                    await client.get(
                        f"{prefix}/tenants/private-tenant-{index}/records/private-reader-{index}?token=private-secret"
                    )
                ).status_code == 200

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert len(points) == 1
    assert points[0].count == 5
    template = prefix + "/tenants/{tenant_id}/records/{identifier}"
    assert points[0].attributes["http.route"] == template
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 5
    assert all(item.name == "GET " + template for item in servers)
    payload = generate_latest(runtime.metric_server.registry).decode() + str(
        [(item.name, item.attributes, item.events) for item in servers]
    )
    assert "private-" not in payload
    assert "token=" not in payload


@pytest.mark.parametrize("lane", ["request", "stream"])
def test_admission_rejection_preserves_template_route_and_private_attributes(
    observed_runtime, lane
):
    from devfeed_http.admission import AdmissionMiddleware

    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())
    app.add_middleware(AdmissionMiddleware, requests=1, streams=1)

    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()

        @app.get(
            "/records/{identifier}",
            openapi_extra=RouteRuntimePolicy(admission=lane).openapi_extra(),
        )
        async def blocked(identifier: str):
            started.set()
            await finish.wait()
            return {"ok": True}

        install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            active = asyncio.create_task(client.get("/records/private-first?token=private-secret"))
            await asyncio.wait_for(started.wait(), 2)
            try:
                for index in range(5):
                    assert (
                        await client.get(f"/records/private-{index}?token=private-secret")
                    ).status_code == 503
            finally:
                finish.set()
                assert (await active).status_code == 200

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {
        (point.attributes["http.route"], point.attributes["http.response.status_code"]): point.count
        for point in points
    } == {("/records/{identifier}", 503): 5, ("/records/{identifier}", 200): 1}
    assert {point.attributes["devfeed.request.kind"] for point in points} == {lane}
    assert otel_points(runtime, "devfeed.admission.rejections")[0].value == 5
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 6
    assert all(item.name == "GET /records/{identifier}" for item in servers)
    payload = generate_latest(runtime.metric_server.registry).decode() + str(
        [(item.name, item.attributes, item.events) for item in servers]
    )
    assert "private-" not in payload
    assert "token=" not in payload


def test_runtime_policy_uses_native_method_selection_and_records_unhandled_errors(
    observed_runtime,
):
    from fastapi import HTTPException

    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())

    @app.get(
        "/quiet/{identifier}",
        openapi_extra=RouteRuntimePolicy(
            admission="exempt", telemetry_excluded=True, logging_level="debug"
        ).openapi_extra(),
    )
    def quiet(identifier: str):
        return {"ok": True}

    @app.get("/handled/{identifier}")
    def handled(identifier: str):
        raise HTTPException(status_code=409, detail="private-user-input")

    @app.get("/broken/{identifier}")
    def broken(identifier: str):
        raise RuntimeError("private-token-and-user-input")

    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            assert (await client.get("/quiet/private-id?token=private-query")).status_code == 200
            assert (await client.post("/quiet/private-id?token=private-query")).status_code == 405
            assert (await client.get("/handled/private-id?token=private-query")).status_code == 409
            assert (await client.get("/broken/private-id?token=private-query")).status_code == 500

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {
        (point.attributes["http.route"], point.attributes["http.response.status_code"]): point.count
        for point in points
    } == {
        ("/quiet/{identifier}", 405): 1,
        ("/handled/{identifier}", 409): 1,
        ("/broken/{identifier}", 500): 1,
    }
    assert {point.attributes["devfeed.request.kind"] for point in points} == {"request"}
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 3
    assert {
        (item.name, item.attributes["http.response.status_code"]): item.status.status_code
        for item in servers
    } == {
        ("POST /quiet/{identifier}", 405): telemetry.trace.StatusCode.UNSET,
        ("GET /handled/{identifier}", 409): telemetry.trace.StatusCode.UNSET,
        ("GET /broken/{identifier}", 500): telemetry.trace.StatusCode.ERROR,
    }
    payload = generate_latest(runtime.metric_server.registry).decode() + str(
        [(item.name, item.attributes, item.events) for item in servers]
    )
    assert "private-" not in payload
    assert "token=" not in payload
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

        install_runtime_policies(app)

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


def test_pre_routing_cors_and_slash_redirects_keep_native_telemetry_and_admission(
    observed_runtime, monkeypatch
):
    from devfeed_api.main import create_app

    runtime, exporter = observed_runtime
    monkeypatch.setenv("DEVFEED_API_MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("DEVFEED_API_MAX_CONCURRENT_STREAMS", "1")
    monkeypatch.setenv("DEVFEED_CORS_ORIGINS", '["https://reader.test"]')
    get_settings.cache_clear()
    app = create_app()

    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()

        @app.get("/held")
        async def held():
            started.set()
            await finish.wait()
            return {"ok": True}

        @app.get("/redirected/")
        async def redirected():
            return {"ok": True}

        install_runtime_policies(app)
        preflight = {"Origin": "https://reader.test", "Access-Control-Request-Method": "GET"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            allowed = await client.options("/held", headers=preflight)
            assert allowed.status_code == 200
            assert allowed.headers["access-control-allow-origin"] == "https://reader.test"
            redirect = await client.get("/redirected")
            assert redirect.status_code == 307
            assert redirect.headers["location"] == "http://test/redirected/"
            active = asyncio.create_task(client.get("/held"))
            await asyncio.wait_for(started.wait(), 2)
            try:
                rejected = await client.options("/held", headers=preflight)
                assert rejected.status_code == 503
                assert rejected.headers["access-control-allow-origin"] == "https://reader.test"
                assert rejected.headers["retry-after"] == "1"
            finally:
                finish.set()
                assert (await active).status_code == 200

    asyncio.run(exercise())
    points = otel_points(runtime, "http.server.request.duration")
    assert {
        (point.attributes["http.request.method"], point.attributes["http.response.status_code"]): (
            point.count
        )
        for point in points
    } == {("OPTIONS", 200): 1, ("GET", 307): 1, ("OPTIONS", 503): 1, ("GET", 200): 1}
    assert {point.attributes["devfeed.request.kind"] for point in points} == {"request"}
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 4
    assert all(point.value == 0 for point in otel_points(runtime, "http.server.active_requests"))
    assert otel_points(runtime, "devfeed.admission.rejections")[0].value == 1
    assert otel_points(runtime, "devfeed.admission.active")[0].value == 0


@pytest.mark.parametrize("failure", ["send", "cancel", "anyio"])
def test_pre_routing_response_observer_cleans_up_failed_or_cancelled_sends(
    observed_runtime, failure
):
    import gc

    import anyio
    from starlette.middleware.cors import CORSMiddleware

    runtime, exporter = observed_runtime
    app = FastAPI(telemetry=fastapi_telemetry())
    app.add_middleware(CORSMiddleware, allow_origins=["https://reader.test"], allow_methods=["GET"])
    install_runtime_policies(app)

    async def exercise():
        entered, never = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                entered.set()
                if failure == "send":
                    raise OSError("reader disconnected")
                await never.wait()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "OPTIONS",
            "path": "/unknown-private-id",
            "raw_path": b"/unknown-private-id",
            "root_path": "",
            "query_string": b"token=private-secret",
            "scheme": "http",
            "http_version": "1.1",
            "headers": [
                (b"origin", b"https://reader.test"),
                (b"access-control-request-method", b"GET"),
            ],
        }
        if failure == "send":
            with pytest.raises(OSError, match="reader disconnected"):
                await app(scope, receive, send)
        elif failure == "anyio":
            with anyio.move_on_after(0.02) as deadline:
                await app(scope, receive, send)
            assert deadline.cancel_called
            assert entered.is_set()
        else:
            active = asyncio.create_task(app(scope, receive, send))
            await asyncio.wait_for(entered.wait(), 2)
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == {asyncio.current_task()}
        gc.collect()
        await asyncio.sleep(0)
        assert errors == []

    asyncio.run(exercise())
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 1
    assert all(point.value == 0 for point in otel_points(runtime, "http.server.active_requests"))
    payload = generate_latest(runtime.metric_server.registry).decode() + str(
        [(item.name, item.attributes, item.events) for item in servers]
    )
    assert "private-" not in payload
    assert "token=" not in payload


@pytest.mark.parametrize("failure", [False, True])
def test_completion_and_failure_logs_keep_the_matched_native_server_trace(
    observed_runtime, caplog, failure
):
    from devfeed_http.logging import RequestLoggingMiddleware

    _, exporter = observed_runtime
    request_logger = logging.getLogger("devfeed_http.runtime-test")
    caplog.set_level(logging.INFO, logger=request_logger.name)
    app = FastAPI(telemetry=fastapi_telemetry())
    app.add_middleware(RequestLoggingMiddleware, service="api", logger=request_logger)

    @app.get("/correlated/{identifier}")
    def endpoint(identifier: str):
        if failure:
            raise RuntimeError("endpoint failed")
        return {"ok": True}

    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            return await client.get("/correlated/private-id")

    response = asyncio.run(exercise())
    server = next(
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    )
    records = [
        item
        for item in caplog.records
        if item.name == request_logger.name and item.msg in {"request_completed", "request_failed"}
    ]
    assert [item.msg for item in records] == (
        ["request_failed", "request_completed"] if failure else ["request_completed"]
    )
    assert all(item.trace_id == format(server.context.trace_id, "032x") for item in records)
    assert all(item.span_id == format(server.context.span_id, "016x") for item in records)
    assert response.status_code == (500 if failure else 200)
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("order", [(500, Exception), (Exception, 500)])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_server_error_handler_runs_once_and_preserves_native_registration_precedence(
    observed_runtime, caplog, order, mode
):
    from devfeed_http.logging import RequestLoggingMiddleware
    from fastapi.responses import JSONResponse

    runtime, exporter = observed_runtime
    request_logger = logging.getLogger("devfeed_http.native-server-errors")
    caplog.set_level(logging.ERROR, logger=request_logger.name)
    app = FastAPI(telemetry=fastapi_telemetry())
    app.add_middleware(RequestLoggingMiddleware, service="api", logger=request_logger)
    calls = []

    def make_handler(kind):
        def handle(request, exc):
            calls.append((kind, exc))
            return JSONResponse(
                {"handler": "exception" if kind is Exception else "500", "calls": len(calls)},
                status_code=500,
            )

        async def async_handle(request, exc):
            return handle(request, exc)

        return handle if mode == "sync" else async_handle

    for kind in order:
        app.add_exception_handler(kind, make_handler(kind))

    @app.get("/failed/{identifier}")
    def endpoint(identifier: str):
        raise RuntimeError("original endpoint failure")

    install_runtime_policies(app)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.get("/failed/private-first")
            assert response.status_code == 500
            assert response.json() == {
                "handler": "exception" if order[-1] is Exception else "500",
                "calls": 1,
            }
        # Native error middleware sends the response and still propagates the
        # original exception to a server or a test client configured to raise.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with pytest.raises(RuntimeError, match="original endpoint failure") as error:
                await client.get("/failed/private-second")
            assert error.value is calls[-1][1]

    asyncio.run(exercise())
    assert [kind for kind, _ in calls] == [order[-1], order[-1]]
    failures = [
        record
        for record in caplog.records
        if record.name == request_logger.name and record.msg == "request_failed"
    ]
    assert len(failures) == 2
    assert [record.exc_info[1] for record in failures] == [exc for _, exc in calls]
    servers = [
        item
        for item in exporter.get_finished_spans()
        if item.kind == telemetry.trace.SpanKind.SERVER
    ]
    assert len(servers) == 2
    assert all(item.attributes["http.response.status_code"] == 500 for item in servers)
    assert all(item.status.status_code == telemetry.trace.StatusCode.ERROR for item in servers)
    points = otel_points(runtime, "http.server.request.duration")
    assert len(points) == 1 and points[0].count == 2
    assert points[0].attributes["http.route"] == "/failed/{identifier}"
    assert points[0].attributes["http.response.status_code"] == 500


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_raising_server_error_handler_matches_native_single_call_and_exception(mode):
    async def exercise(installed):
        app = FastAPI(telemetry=fastapi_telemetry())
        calls = []
        handler_error = ValueError("custom handler failure")

        def handle(request, exc):
            calls.append(exc)
            raise handler_error

        async def async_handle(request, exc):
            return handle(request, exc)

        app.add_exception_handler(500, handle if mode == "sync" else async_handle)

        @app.get("/failed")
        def endpoint():
            raise RuntimeError("original endpoint failure")

        if installed:
            install_runtime_policies(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            with pytest.raises(ValueError, match="custom handler failure") as error:
                await client.get("/failed")
            assert error.value is handler_error
        return calls

    calls_by_app = [asyncio.run(exercise(installed)) for installed in (False, True)]
    assert [len(calls) for calls in calls_by_app] == [1, 1]
    assert all(isinstance(calls[0], RuntimeError) for calls in calls_by_app)


def test_mounted_error_handlers_preserve_native_callback_order_and_child_response():
    from fastapi.responses import JSONResponse

    async def exercise(installed):
        app = FastAPI(telemetry=fastapi_telemetry())
        child = FastAPI(telemetry=fastapi_telemetry())
        calls = []

        async def parent_handler(request, exc):
            calls.append(("parent", exc))
            return JSONResponse({"handler": "parent"}, status_code=500)

        async def child_handler(request, exc):
            calls.append(("child", exc))
            return JSONResponse({"handler": "child"}, status_code=500)

        app.add_exception_handler(500, parent_handler)
        child.add_exception_handler(Exception, child_handler)

        @child.get("/failed")
        def endpoint():
            raise RuntimeError("original endpoint failure")

        app.mount("/nested", child)
        if installed:
            install_runtime_policies(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.get("/nested/failed")
        assert response.status_code == 500
        assert response.json() == {"handler": "child"}
        assert all(isinstance(exc, RuntimeError) for _, exc in calls)
        assert all(exc is calls[0][1] for _, exc in calls)
        return [name for name, _ in calls]

    assert asyncio.run(exercise(False)) == ["child", "parent"]
    assert asyncio.run(exercise(True)) == ["child", "parent"]


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
    assert all(point.attributes["http.route"] == "/v1/feed" for point in points)
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
    record_cache_read("public_response", "miss", route="/v1/articles/private-id?code=private-code")
    points = otel_points(runtime, "devfeed.cache.reads")
    assert {point.attributes["cache.name"] for point in points} == {
        "admin_overview",
        "admin_panels",
        "public_profile",
        "feed_sequence",
        "other",
        "public_response",
    }
    assert "private-" not in str([point.attributes for point in points])
    assert (
        next(
            point.attributes["http.route"]
            for point in points
            if point.attributes["cache.name"] == "public_response"
        )
        == "other"
    )


def test_public_cache_routes_are_bounded_and_complete():
    from devfeed_api import feed, search, sources, taxonomy, topics
    from devfeed_api.cache import CachedReadRoute
    from devfeed_core.cache import PUBLIC_CACHE_ROUTES

    assert {
        route.path
        for module in (feed, search, sources, taxonomy, topics)
        for route in module.router.routes
        if isinstance(route, CachedReadRoute) and "GET" in route.methods
    } == PUBLIC_CACHE_ROUTES


def test_cache_route_templates_separate_reads_without_query_or_identifier_labels(
    observed_runtime, cached_client
):
    import uuid

    from devfeed_core.models import Source, utcnow

    runtime, _ = observed_runtime
    client, session, *_ = cached_client
    session.get = lambda *args: Source(
        id=args[1],
        name="Example",
        feed_url="https://example.com/rss",
        source_type="publisher",
        slug="example",
        created_at=utcnow(),
        approval_status="approved",
        enabled=True,
    )
    identifiers = [str(uuid.uuid4()) for _ in range(3)]
    for identifier in identifiers:
        path = f"/v1/sources/{identifier}?unused=private-query"
        assert client.get(path).headers["X-Cache"] == "MISS"
        assert client.get(path).headers["X-Cache"] == "HIT"
    for path in ("/v1/feed", "/v1/tags"):
        assert client.get(path).headers["X-Cache"] == "MISS"
        assert client.get(path).headers["X-Cache"] == "HIT"
    points = otel_points(runtime, "devfeed.cache.reads")
    assert sum(point.value for point in points) == 10
    assert {point.attributes["http.route"] for point in points} == {
        "/v1/sources/{source_id}",
        "/v1/feed",
        "/v1/tags",
    }
    exposition = generate_latest(runtime.metric_server.registry).decode()
    assert 'http_route="/v1/sources/{source_id}"' in exposition
    assert "private-query" not in exposition
    assert not any(identifier in exposition for identifier in identifiers)


def test_coalesced_public_cache_reads_count_final_decisions_without_poll_attempts(
    observed_runtime, cached_client
):
    from concurrent.futures import ThreadPoolExecutor

    runtime, _ = observed_runtime
    client, session, *_ = cached_client
    original = session.scalars

    def slow(statement):
        time.sleep(0.1)
        return original(statement)

    session.scalars = slow
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.get("/v1/feed"), range(8)))
    assert sum(response.headers["X-Cache"] == "MISS" for response in responses) == 1
    points = otel_points(runtime, "devfeed.cache.reads")
    assert sum(point.value for point in points) == 8
    assert next(point.value for point in points if point.attributes["cache.outcome"] == "hit") == 7


def test_cache_invalidations_count_once_and_deduplicate_bounded_causes(
    observed_runtime, response_cache, monkeypatch
):
    from devfeed_core.cache import invalidate_public_cache, record_cache_invalidation
    from redis.exceptions import ConnectionError

    runtime, _ = observed_runtime
    invalidate_public_cache(reasons=("sources", "articles", "articles"))
    response_cache.invalidate("operations", reasons=("manual",))
    invalidate_public_cache(reasons=("private-table", "private-other-table"))
    invalidate_public_cache(reasons=())
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    invalidate_public_cache(reasons=("profile",))
    monkeypatch.setattr(get_settings(), "cache_enabled", True)

    def fail(*args, **kwargs):
        raise ConnectionError("private-redis-credentials")

    monkeypatch.setattr(response_cache.redis, "set", fail)
    # Failure is measured but remains suppressed after successful database writes.
    invalidate_public_cache(reasons=("avatar",))
    invalidate_public_cache(reasons=("search_index",))
    record_cache_invalidation("private-domain", "private-outcome", ("private-reason",))
    points = otel_points(runtime, "devfeed.cache.invalidations")
    assert sum(point.value for point in points) == 8
    assert {point.attributes["cache.outcome"] for point in points} == {
        "success",
        "cache_unavailable",
        "disabled",
        "other",
    }
    assert (
        next(
            point.value
            for point in points
            if point.attributes == {"cache.domain": "public", "cache.outcome": "success"}
        )
        == 3
    )
    causes = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert sum(point.value for point in causes) == 9
    assert {point.attributes["cache.invalidation_reason"] for point in causes} == {
        "sources",
        "articles",
        "manual",
        "other",
        "unspecified",
        "profile",
        "avatar",
        "search_index",
    }
    exposition = generate_latest(runtime.metric_server.registry).decode()
    assert "devfeed_cache_invalidations_total" in exposition
    assert "devfeed_cache_invalidation_causes_total" in exposition
    assert "private-" not in exposition


def test_manual_clear_counts_each_domain_once(observed_runtime, response_cache, monkeypatch):
    from devfeed_cli import commands

    runtime, _ = observed_runtime
    monkeypatch.setattr(commands, "get_cache", lambda: response_cache)
    assert commands.cache_clear(None)["cleared"]
    points = otel_points(runtime, "devfeed.cache.invalidations")
    assert sum(point.value for point in points) == 2
    assert {point.attributes["cache.domain"] for point in points} == {"public", "operations"}
    causes = otel_points(runtime, "devfeed.cache.invalidation.causes")
    assert sum(point.value for point in causes) == 2
    assert all(point.attributes["cache.invalidation_reason"] == "manual" for point in causes)


@pytest.mark.parametrize("service", ["worker-ingestion", "cli"])
def test_short_lived_cache_counters_export_deltas_across_flushes_and_lifetimes(
    monkeypatch, response_cache, service
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from devfeed_core.cache import invalidate_public_cache
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )
    from opentelemetry.proto.metrics.v1.metrics_pb2 import AGGREGATION_TEMPORALITY_DELTA

    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            message = ExportMetricsServiceRequest()
            message.ParseFromString(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(message)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEVFEED_OTLP_ENDPOINT", f"http://127.0.0.1:{collector.server_port}")
    monkeypatch.setenv("HOSTNAME", "worker-pod")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "CUMULATIVE")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    try:
        for counts in ((1, 2), (3,)):
            runtime = telemetry.start_runtime(service, serve_metrics=False, profiling=False)
            try:
                for count in counts:
                    for _ in range(count):
                        invalidate_public_cache(reasons=("articles",))
                    if len(counts) > 1:
                        assert runtime.meter_provider.force_flush(timeout_millis=2000)
            finally:
                telemetry.stop_runtime(runtime)
        for name in ("devfeed.cache.invalidations", "devfeed.cache.invalidation.causes"):
            sums = [
                metric.sum
                for batch in received
                for resource in batch.resource_metrics
                for scope in resource.scope_metrics
                for metric in scope.metrics
                if metric.name == name
            ]
            assert sums
            assert all(
                item.aggregation_temporality == AGGREGATION_TEMPORALITY_DELTA for item in sums
            )
            assert [
                point.as_int for item in sums for point in item.data_points if point.as_int
            ] == [1, 2, 3]
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=1)


def test_parent_cache_counters_keep_cumulative_prometheus_reads(monkeypatch):
    from devfeed_core.cache import record_cache_invalidation

    monkeypatch.setattr(get_settings(), "metrics_enabled", True)
    runtime = telemetry.Runtime("worker-ingestion", get_settings())
    runtime.start_metrics(serve_metrics=True, export_otlp=False)
    monkeypatch.setattr(telemetry, "_runtime", runtime)
    try:
        for expected in (1, 2):
            record_cache_invalidation("public", "success", ("articles",))
            payload = generate_latest(runtime.metric_server.registry).decode()
            line = next(
                line
                for line in payload.splitlines()
                if line.startswith("devfeed_cache_invalidations_total{")
            )
            assert float(line.rsplit(" ", 1)[1]) == expected
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "command", ["cache", "sources", "images", "discovery", "worker", "scheduler", "search"]
)
@pytest.mark.parametrize("reuse", [False, True])
def test_cli_writers_flush_their_runtime_without_taking_background_ownership(
    monkeypatch, command, reuse
):
    from types import SimpleNamespace

    from devfeed_cli import runtime

    owned = object()
    starts, stops = [], []
    monkeypatch.setattr(runtime, "current", lambda: owned if reuse else None)

    def start(service, **kwargs):
        starts.append((service, kwargs))
        return owned

    monkeypatch.setattr(runtime, "start_runtime", start)
    monkeypatch.setattr(runtime, "stop_runtime", stops.append)
    args = SimpleNamespace(command=command, action="worker", execute=lambda _: None)
    assert runtime.execute(args) == 0
    if command in {"cache", "sources", "images", "discovery"} and not reuse:
        assert starts == [("cli", {"serve_metrics": False, "profiling": False})]
        assert stops == [owned]
    else:
        assert not starts and not stops


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

    install_runtime_policies(app)

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

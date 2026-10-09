"""Optional, fail-open trace/profiling export with explicit process ownership.

Application spans use an allowlist of attributes, never SQL statements, request
bodies, credentials, article contents, or exception messages. RQ starts this
runtime *after* fork and bounds shutdown before the work horse calls os._exit.
"""

import inspect
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import Counter, MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from devfeed_core.config import Settings, get_settings
from devfeed_core.metrics import DURATION_BUCKETS, MetricServer
from devfeed_core.version import __version__

logger = logging.getLogger(__name__)
_runtime: "Runtime | None" = None
_propagator = TraceContextTextMapPropagator()  # Never propagate arbitrary baggage.


def current() -> "Runtime | None":
    return _runtime if _runtime and _runtime.pid == os.getpid() else None


def trace_fields() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def inject_context() -> dict[str, str]:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier


def extract_context(carrier):
    # Only traceparent is accepted. Tracestate/baggage may carry user identifiers.
    parent = carrier.get("traceparent", "") if isinstance(carrier, dict) else ""
    return _propagator.extract(
        {"traceparent": parent[:55] if isinstance(parent, str) and len(parent) == 55 else ""}
    )


def bounded_shutdown(callback, timeout: float = 2) -> None:
    def run():
        try:
            callback()
        except Exception:
            logger.warning("telemetry_shutdown_failed")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.warning("telemetry_shutdown_timed_out")


class Runtime:
    def __init__(self, service: str, settings: Settings):
        self.pid = os.getpid()
        self.service = service
        self.settings = settings
        self.metric_server = MetricServer()
        self.provider: TracerProvider | None = None
        self.meter_provider: MeterProvider | None = None
        self.instruments: dict = create_instruments(metrics.NoOpMeter("devfeed"))
        self.profiler = None

    def resource_attributes(self) -> dict[str, str]:
        return {
            "service.name": "devfeed-" + self.service,
            "service.instance.id": os.environ.get("HOSTNAME") or str(self.pid),
            "service.version": __version__,
            "deployment.environment.name": self.settings.telemetry_environment,
        }

    def start(self, *, serve_metrics: bool, profiling: bool, tracing: bool) -> None:
        if serve_metrics and self.settings.metrics_enabled:
            try:
                self.metric_server.listen(self.settings.metrics_host, self.settings.metrics_port)
            except OSError:
                logger.warning("metrics_listener_initialization_failed")
        if tracing and self.settings.otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                self.provider = TracerProvider(
                    resource=Resource(self.resource_attributes()),
                    sampler=ParentBased(TraceIdRatioBased(self.settings.trace_sample_ratio)),
                    shutdown_on_exit=False,
                )
                exporter = OTLPSpanExporter(
                    endpoint=self.settings.otlp_endpoint.rstrip("/") + "/v1/traces",
                    timeout=1,
                )
                self.provider.add_span_processor(
                    BatchSpanProcessor(
                        exporter,
                        max_queue_size=512,
                        max_export_batch_size=64,
                        schedule_delay_millis=1000,
                        export_timeout_millis=1000,
                    )
                )
            except Exception:
                logger.warning("tracing_initialization_failed")
        self.start_metrics(serve_metrics=serve_metrics, export_otlp=tracing)
        if profiling and self.settings.pyroscope_server:
            try:
                import pyroscope

                pyroscope.configure(
                    application_name="devfeed-" + self.service,
                    server_address=self.settings.pyroscope_server,
                    sample_rate=self.settings.profiling_sample_rate,
                    oncpu=True,
                    gil_only=True,
                    mem_enabled=True,
                    mem_heap_sample_size=1_048_576,
                    mem_max_nframe=64,
                    tags={
                        "environment": self.settings.telemetry_environment,
                        "version": __version__,
                    },
                    report_pid=False,
                    report_thread_id=False,
                    report_thread_name=False,
                )
                self.profiler = pyroscope
            except Exception:
                logger.warning("profiling_initialization_failed")

    def start_metrics(self, readers=None, *, serve_metrics=True, export_otlp=True) -> None:
        """One owned metric pipeline; readers can be supplied by regression tests."""
        try:
            if readers is None:
                readers = []
                if self.settings.metrics_enabled and serve_metrics:
                    from opentelemetry.exporter.prometheus import PrometheusMetricReader

                    readers.append(
                        PrometheusMetricReader(
                            registry=self.metric_server.registry,
                            scope_info_enabled=False,
                            resource_attribute_filter=lambda key: key == "service.name",
                        )
                    )
                if self.settings.otlp_endpoint and export_otlp:
                    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                        OTLPMetricExporter,
                    )
                    from opentelemetry.sdk.metrics.export import (
                        AggregationTemporality,
                        PeriodicExportingMetricReader,
                    )

                    readers.append(
                        PeriodicExportingMetricReader(
                            OTLPMetricExporter(
                                endpoint=self.settings.otlp_endpoint.rstrip("/") + "/v1/metrics",
                                timeout=1,
                                # Short-lived writers reuse the pod identity but
                                # reset counters every invocation. Export
                                # increments so the collector can accumulate
                                # invalidations across
                                # children, including their final shutdown flush.
                                preferred_temporality=(
                                    {Counter: AggregationTemporality.DELTA}
                                    if not serve_metrics
                                    and (
                                        self.service.startswith("worker-") or self.service == "cli"
                                    )
                                    else None
                                ),
                            ),
                            export_interval_millis=60000,
                            export_timeout_millis=1000,
                        )
                    )
            self.meter_provider = MeterProvider(
                resource=Resource(self.resource_attributes()),
                metric_readers=readers,
                shutdown_on_exit=False,
            )
            meter = self.meter_provider.get_meter("devfeed")
            self.instruments = create_instruments(meter)
        except Exception:
            logger.warning("otel_metrics_initialization_failed")

    def close(self) -> None:
        if self.pid != os.getpid():
            return
        if self.provider:
            bounded_shutdown(self.provider.shutdown)
        if self.meter_provider:
            bounded_shutdown(self.meter_provider.shutdown)
        if self.profiler:
            bounded_shutdown(self.profiler.shutdown)
        self.metric_server.close()


def start_runtime(
    service: str, *, serve_metrics=True, profiling=True, tracing=True
) -> Runtime | None:
    global _runtime
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return None
    settings = get_settings()
    if not (settings.metrics_enabled or settings.otlp_endpoint or settings.pyroscope_server):
        return None
    if current():
        return current()
    runtime = Runtime(service, settings)
    runtime.start(serve_metrics=serve_metrics, profiling=profiling, tracing=tracing)
    _runtime = runtime
    return runtime


def stop_runtime(runtime: Runtime | None) -> None:
    global _runtime
    if runtime:
        runtime.close()
    if _runtime is runtime:
        _runtime = None


@contextmanager
def span(name: str, *, attributes=None, context=None, kind=trace.SpanKind.INTERNAL) -> Iterator:
    runtime = current()
    tracer = (
        runtime.provider.get_tracer("devfeed")
        if runtime and runtime.provider
        else trace.NoOpTracer()
    )
    with tracer.start_as_current_span(
        name,
        attributes=attributes,
        context=context,
        kind=kind,
        record_exception=False,
        set_status_on_exception=False,
    ) as active:
        try:
            yield active
        except BaseException as exc:
            active.set_status(trace.StatusCode.ERROR)
            active.set_attribute("error.type", type(exc).__name__)
            raise


@contextmanager
def background_cycle(name: str) -> Iterator:
    started = time.monotonic()
    result = "error"
    try:
        with span(name):
            yield
        result = "success"
    finally:
        if runtime := current():
            attributes = {"cycle": name, "outcome": result}
            runtime.instruments["cycles"].add(1, attributes)
            runtime.instruments["cycle_duration"].record(time.monotonic() - started, attributes)
            if result == "success":
                runtime.instruments["last_success"].set(time.time(), {"cycle": name})


@contextmanager
def dependency_call(dependency: str, operation: str) -> Iterator:
    started = time.monotonic()
    result = "error"
    try:
        with span(
            f"{dependency}.{operation}",
            attributes={"peer.service": dependency},
            kind=trace.SpanKind.CLIENT,
        ):
            yield
        result = "success"
    finally:
        if (runtime := current()) and (instrument := runtime.instruments.get("dependency")):
            instrument.record(
                time.monotonic() - started,
                {"dependency": dependency, "operation": operation, "outcome": result},
            )


def observed_dependency(dependency: str, operation: str):
    """Names come from source constants; never derive labels from URLs or inputs."""

    def decorate(function):
        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with dependency_call(dependency, operation):
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with dependency_call(dependency, operation):
                return function(*args, **kwargs)

        return synchronous

    return decorate


def create_instruments(meter):
    return {
        "polling_interval": meter.create_histogram(
            "devfeed.source.poll_interval",
            unit="s",
            explicit_bucket_boundaries_advisory=(300, 900, 3600, 10800, 43200, 86400, 604800),
        ),
        "polling_new_entries": meter.create_counter(
            "devfeed.source.fetch_with_new_entries", unit="{fetch}"
        ),
        "polling_unchanged": meter.create_counter("devfeed.source.unchanged", unit="{fetch}"),
        "polling_queue_delay": meter.create_histogram(
            "devfeed.source.queue_delay",
            unit="s",
            explicit_bucket_boundaries_advisory=(1, 10, 60, 300, 3600, 86400),
        ),
        "polling_discovery_delay": meter.create_histogram(
            "devfeed.source.discovery_delay",
            unit="s",
            explicit_bucket_boundaries_advisory=(60, 300, 3600, 10800, 43200, 86400, 604800),
        ),
        "worker_active": meter.create_gauge("devfeed.worker.active", unit="{worker}"),
        "executions": meter.create_counter("devfeed.worker.executions", unit="{execution}"),
        "execution_duration": meter.create_histogram(
            "messaging.process.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=(1, 5, 15, 30, 60, 120, 240, 300),
        ),
        "cycles": meter.create_counter("devfeed.background.cycles", unit="{cycle}"),
        "cycle_duration": meter.create_histogram(
            "devfeed.background.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        "last_success": meter.create_gauge("devfeed.background.last_success", unit="s"),
        "search_documents": meter.create_counter("devfeed.search.documents", unit="{document}"),
        "search_outbox": meter.create_gauge("devfeed.search.outbox", unit="{event}"),
        "search_oldest": meter.create_gauge("devfeed.search.oldest_event_age", unit="s"),
        "dependency": meter.create_histogram(
            "devfeed.dependency.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        "database": meter.create_histogram(
            "db.client.operation.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        "cache": meter.create_counter("devfeed.cache.reads", unit="{read}"),
        "cache_invalidations": meter.create_counter(
            "devfeed.cache.invalidations", unit="{invalidation}"
        ),
        "cache_invalidation_causes": meter.create_counter(
            "devfeed.cache.invalidation.causes", unit="{cause}"
        ),
        "db_wait": meter.create_histogram(
            "db.client.connection.wait_time",
            unit="s",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        "db_connections": meter.create_up_down_counter(
            "db.client.connection.count", unit="{connection}"
        ),
        "rejections": meter.create_counter("devfeed.admission.rejections", unit="{request}"),
        "admitted": meter.create_up_down_counter("devfeed.admission.active", unit="{request}"),
        "limit": meter.create_gauge("devfeed.admission.limit", unit="{request}"),
    }

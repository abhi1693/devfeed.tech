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

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from devfeed_core.config import Settings, get_settings
from devfeed_core.metrics import Metrics
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
        self.metrics = Metrics(service, settings.telemetry_environment)
        self.provider: TracerProvider | None = None
        self.profiler = None

    def start(self, *, serve_metrics: bool, profiling: bool, tracing: bool) -> None:
        if serve_metrics and self.settings.metrics_enabled:
            self.metrics.listen(self.settings.metrics_host, self.settings.metrics_port)
        if tracing and self.settings.otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                self.provider = TracerProvider(
                    resource=Resource.create(
                        {
                            "service.name": "devfeed-" + self.service,
                            "service.version": __version__,
                            "deployment.environment.name": self.settings.telemetry_environment,
                        }
                    ),
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
                self.metrics.component_up.labels(self.service, "tracing").set(1)
            except Exception:
                self.metrics.component_up.labels(self.service, "tracing").set(0)
                logger.warning("tracing_initialization_failed")
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
                self.metrics.component_up.labels(self.service, "profiling").set(1)
            except Exception:
                self.metrics.component_up.labels(self.service, "profiling").set(0)
                logger.warning("profiling_initialization_failed")

    def close(self) -> None:
        if self.pid != os.getpid():
            return
        if self.provider:
            bounded_shutdown(self.provider.shutdown)
        if self.profiler:
            bounded_shutdown(self.profiler.shutdown)
        self.metrics.close()


def start_runtime(
    service: str, *, serve_metrics=True, profiling=True, tracing=True
) -> Runtime | None:
    global _runtime
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
            metrics = runtime.metrics
            metrics.cycles.labels(runtime.service, result).inc()
            metrics.cycle_duration.labels(runtime.service).observe(time.monotonic() - started)
            if result == "success":
                metrics.last_success.labels(runtime.service).set(time.time())


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
        if runtime := current():
            runtime.metrics.dependency_duration.labels(
                runtime.service, dependency, operation, result
            ).observe(time.monotonic() - started)


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

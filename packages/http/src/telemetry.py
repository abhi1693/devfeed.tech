"""FastAPI native telemetry with process-owned providers and private attributes.

Factories do not start exporters. These small adapters resolve the runtime after
ASGI lifespan startup (or an RQ fork), without changing OTel global providers.
"""

from collections.abc import Callable, Mapping, Sequence

from devfeed_core.telemetry import current
from fastapi.telemetry import TelemetryConfig, get_telemetry_data
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.util._decorator import _AgnosticContextManager
from opentelemetry.util.types import AttributeValue
from starlette.types import Scope

from devfeed_http.route_policies import (
    CURRENT_ROUTE_SCOPE,
    RUNTIME_POLICY_SCOPE_KEY,
    RouteRuntimePolicy,
)

TRACE_ATTRIBUTES = frozenset(
    {
        "http.request.method",
        "url.scheme",
        "network.protocol.version",
        "network.protocol.name",
        "code.function.name",
    }
)


class RuntimeTracerProvider(trace.TracerProvider):
    def get_tracer(
        self,
        instrumenting_module_name: str,
        instrumenting_library_version: str | None = None,
        schema_url: str | None = None,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> trace.Tracer:
        runtime = current()
        delegate = (
            runtime.provider.get_tracer(
                instrumenting_module_name, instrumenting_library_version, schema_url=schema_url
            )
            if runtime and runtime.provider
            else trace.NoOpTracer()
        )
        return PrivateTracer(delegate)


class PrivateTracer(trace.Tracer):
    def __init__(self, delegate: trace.Tracer) -> None:
        self.delegate = delegate

    def start_span(
        self,
        name: str,
        context: Context | None = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[trace.Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> trace.Span:
        if kind == trace.SpanKind.SERVER:
            # Native FastAPI extracts global propagators. Keep only the W3C
            # parent identity; never export tracestate, baggage or client links.
            parent = trace.get_current_span(context).get_span_context()
            context = Context()
            if parent.is_valid:
                context = trace.set_span_in_context(
                    trace.NonRecordingSpan(
                        trace.SpanContext(
                            trace_id=parent.trace_id,
                            span_id=parent.span_id,
                            is_remote=parent.is_remote,
                            trace_flags=parent.trace_flags,
                        )
                    ),
                    context,
                )
        return self.delegate.start_span(
            name,
            context=context,
            kind=kind,
            start_time=start_time,
            attributes={
                key: value for key, value in (attributes or {}).items() if key in TRACE_ATTRIBUTES
            },
            record_exception=False,
            set_status_on_exception=False,
        )

    def start_as_current_span(
        self,
        name: str,
        context: Context | None = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[trace.Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> _AgnosticContextManager[trace.Span]:
        active = self.start_span(
            name, context=context, kind=kind, attributes=attributes, start_time=start_time
        )
        return trace.use_span(
            active, end_on_exit=end_on_exit, record_exception=False, set_status_on_exception=False
        )


class RuntimeInstrument[T]:
    def __init__(self, factory: Callable[[metrics.Meter], T]) -> None:
        self.factory = factory
        self.provider: MeterProvider | None = None
        self.instrument: T | None = None

    def resolve(self) -> T | None:
        runtime = current()
        provider = runtime.meter_provider if runtime else None
        if provider is None:
            return None
        if self.provider is not provider:
            self.instrument = self.factory(provider.get_meter("fastapi"))
            self.provider = provider
        return self.instrument


class RuntimeHistogram(metrics.NoOpHistogram, RuntimeInstrument[metrics.Histogram]):
    def __init__(
        self,
        name: str,
        unit: str,
        description: str,
        explicit_bucket_boundaries_advisory: Sequence[float] | None,
    ) -> None:
        metrics.NoOpHistogram.__init__(self, name, unit, description)
        RuntimeInstrument.__init__(
            self,
            lambda meter: meter.create_histogram(
                name,
                unit=unit,
                description=description,
                explicit_bucket_boundaries_advisory=explicit_bucket_boundaries_advisory,
            ),
        )

    def record(
        self,
        amount: float,
        attributes: Mapping[str, AttributeValue] | None = None,
        context: Context | None = None,
    ) -> None:
        if instrument := self.resolve():
            data = get_telemetry_data()
            scope = CURRENT_ROUTE_SCOPE.get() or (
                data.request.scope if data and data.request else {}
            )
            streaming = RouteRuntimePolicy.from_scope(scope).admission == "stream"
            instrument.record(
                amount,
                {
                    **(attributes or {}),
                    "devfeed.request.kind": "stream" if streaming else "request",
                },
                context,
            )


class RuntimeUpDownCounter(metrics.NoOpUpDownCounter, RuntimeInstrument[metrics.UpDownCounter]):
    def __init__(self, name: str, unit: str, description: str) -> None:
        metrics.NoOpUpDownCounter.__init__(self, name, unit, description)
        RuntimeInstrument.__init__(
            self,
            lambda meter: meter.create_up_down_counter(
                name,
                unit=unit,
                description=description,
            ),
        )

    def add(
        self,
        amount: float,
        attributes: Mapping[str, AttributeValue] | None = None,
        context: Context | None = None,
    ) -> None:
        if instrument := self.resolve():
            instrument.add(amount, attributes, context)


class RuntimeMeter(metrics.NoOpMeter):
    def create_histogram(
        self,
        name: str,
        unit: str = "",
        description: str = "",
        explicit_bucket_boundaries_advisory: Sequence[float] | None = None,
    ) -> metrics.Histogram:
        return RuntimeHistogram(name, unit, description, explicit_bucket_boundaries_advisory)

    def create_up_down_counter(
        self, name: str, unit: str = "", description: str = ""
    ) -> metrics.UpDownCounter:
        return RuntimeUpDownCounter(name, unit, description)


class RuntimeMeterProvider(metrics.MeterProvider):
    def get_meter(
        self,
        name: str,
        version: str | None = None,
        schema_url: str | None = None,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> metrics.Meter:
        return RuntimeMeter(name, version, schema_url)


def fastapi_telemetry() -> TelemetryConfig:
    return {
        "auto_configure": False,  # Our lifespan owns exactly one export pipeline.
        "tracer_provider": RuntimeTracerProvider(),
        "meter_provider": RuntimeMeterProvider(),
        "logs": False,  # Exception messages can contain credentials or user input.
        "operation_spans": True,
        "exclude": telemetry_excluded,
    }


def telemetry_excluded(scope: Scope) -> bool:
    if current() is None:
        return True
    if RUNTIME_POLICY_SCOPE_KEY in scope:
        return RouteRuntimePolicy.from_scope(scope).telemetry_excluded
    # Installation moves HTTP observation to the matched route boundary.
    app = scope.get("app")
    return scope.get("type") == "http" and (
        app is not None and getattr(app.state, "runtime_policy_binding", None) is not None
    )

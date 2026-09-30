"""FastAPI native telemetry with process-owned providers and private attributes.

Factories do not start exporters. These small adapters resolve the runtime after
ASGI lifespan startup (or an RQ fork), without changing OTel global providers.
"""

from devfeed_core.telemetry import current
from fastapi.telemetry import TelemetryConfig, get_telemetry_data
from opentelemetry import metrics, trace
from opentelemetry.context import Context

from devfeed_http.admission import STREAM_PATHS

EXCLUDED = frozenset({"/health/live", "/health/ready", "/version"})
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
        instrumenting_module_name,
        instrumenting_library_version=None,
        schema_url=None,
        attributes=None,
    ):
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
    def __init__(self, delegate):
        self.delegate = delegate

    def start_span(
        self,
        name,
        context=None,
        kind=trace.SpanKind.INTERNAL,
        attributes=None,
        links=None,
        start_time=None,
        record_exception=True,
        set_status_on_exception=True,
    ):
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
        name,
        context=None,
        kind=trace.SpanKind.INTERNAL,
        attributes=None,
        links=None,
        start_time=None,
        record_exception=True,
        set_status_on_exception=True,
        end_on_exit=True,
    ):
        active = self.start_span(
            name, context=context, kind=kind, attributes=attributes, start_time=start_time
        )
        return trace.use_span(
            active, end_on_exit=end_on_exit, record_exception=False, set_status_on_exception=False
        )


class RuntimeInstrument:
    def __init__(self, factory):
        self.factory = factory
        self.provider = None
        self.instrument = None

    def resolve(self):
        runtime = current()
        provider = runtime.meter_provider if runtime else None
        if provider is None:
            return None
        if self.provider is not provider:
            self.instrument = self.factory(provider.get_meter("fastapi"))
            self.provider = provider
        return self.instrument


class RuntimeHistogram(metrics.NoOpHistogram, RuntimeInstrument):
    def __init__(self, name, unit, description, explicit_bucket_boundaries_advisory):
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

    def record(self, amount, attributes=None, context=None):
        if instrument := self.resolve():
            data = get_telemetry_data()
            streaming = bool(
                data and data.request and data.request.scope.get("path") in STREAM_PATHS
            )
            instrument.record(
                amount,
                {
                    **(attributes or {}),
                    "devfeed.request.kind": "stream" if streaming else "request",
                },
                context,
            )


class RuntimeUpDownCounter(metrics.NoOpUpDownCounter, RuntimeInstrument):
    def __init__(self, name, unit, description):
        metrics.NoOpUpDownCounter.__init__(self, name, unit, description)
        RuntimeInstrument.__init__(
            self,
            lambda meter: meter.create_up_down_counter(
                name,
                unit=unit,
                description=description,
            ),
        )

    def add(self, amount, attributes=None, context=None):
        if instrument := self.resolve():
            instrument.add(amount, attributes, context)


class RuntimeMeter(metrics.NoOpMeter):
    def create_histogram(
        self, name, unit="", description="", explicit_bucket_boundaries_advisory=None
    ):
        return RuntimeHistogram(name, unit, description, explicit_bucket_boundaries_advisory)

    def create_up_down_counter(self, name, unit="", description=""):
        return RuntimeUpDownCounter(name, unit, description)


class RuntimeMeterProvider(metrics.MeterProvider):
    def get_meter(self, name, version=None, schema_url=None, attributes=None):
        return RuntimeMeter(name, version, schema_url)


def fastapi_telemetry() -> TelemetryConfig:
    return {
        "auto_configure": False,  # Our lifespan owns exactly one export pipeline.
        "tracer_provider": RuntimeTracerProvider(),
        "meter_provider": RuntimeMeterProvider(),
        "logs": False,  # Exception messages can contain credentials or user input.
        "operation_spans": True,
        "exclude": lambda scope: current() is None or scope.get("path") in EXCLUDED,
    }

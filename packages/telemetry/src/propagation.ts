import { isSpanContextValid, trace } from "@opentelemetry/api";

// Only trusted internal API clients call this helper. Never copy incoming baggage,
// tracestate or arbitrary request headers into a dependency request.
export function traceHeaders(): Record<string, string> {
  const span = trace.getActiveSpan()?.spanContext();
  if (!span || !isSpanContextValid(span)) return {};
  const flags = (span.traceFlags & 1).toString(16).padStart(2, "0");
  return { traceparent: `00-${span.traceId}-${span.spanId}-${flags}` };
}

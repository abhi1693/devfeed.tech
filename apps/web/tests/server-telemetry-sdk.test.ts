// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { once } from "node:events";
import { context, trace, TraceFlags } from "@opentelemetry/api";
import { registerTelemetry } from "@devfeed/telemetry/server";

afterEach(() => vi.unstubAllEnvs());

it("exports a sampled span through the installed telemetry SDK", async () => {
  const requests: string[] = [];
  const collector = createServer((request, response) => {
    request.resume();
    request.on("end", () => {
      requests.push(request.url || "");
      response.writeHead(200);
      response.end();
    });
  });
  collector.listen(0, "127.0.0.1");
  await once(collector, "listening");
  const collectorPort = (collector.address() as { port: number }).port;
  const reservation = createServer();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const metricsPort = (reservation.address() as { port: number }).port;
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  vi.stubEnv("DEVFEED_METRICS_ENABLED", "true");
  vi.stubEnv("DEVFEED_METRICS_PORT", String(metricsPort));
  vi.stubEnv("DEVFEED_METRICS_HOST", "127.0.0.1");
  vi.stubEnv("DEVFEED_OTLP_ENDPOINT", `http://127.0.0.1:${collectorPort}`);
  vi.stubEnv("DEVFEED_PYROSCOPE_SERVER", "");
  try {
    await registerTelemetry("web");
    const metrics = await (await fetch(`http://127.0.0.1:${metricsPort}/metrics`)).text();
    expect(metrics).toMatch(/devfeed_telemetry_component_up\{[^}]*component="tracing"[^}]*\} 1/);
    const parent = trace.setSpanContext(context.active(), {
      traceId: "a".repeat(32),
      spanId: "b".repeat(16),
      traceFlags: TraceFlags.SAMPLED,
      isRemote: true,
    });
    context.with(parent, () => {
      trace.getTracer("dependency-upgrade-test").startSpan("sampled-export").end();
    });
    await vi.waitFor(() => expect(requests).toContain("/v1/traces"), { timeout: 4000 });
  } finally {
    await new Promise<void>((resolve) => collector.close(() => resolve()));
  }
});

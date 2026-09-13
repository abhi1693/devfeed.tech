import { trace } from "@opentelemetry/api";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { channel } from "node:diagnostics_channel";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { Registry, Counter, Gauge, Histogram, collectDefaultMetrics } from "@prometheus-io/client";
import { observeDeliveries } from "./delivery";
import { routeName, methodName } from "./privacy";
import type { ReadableSpan, SpanExporter } from "@opentelemetry/sdk-trace-base";

export function sanitizeSpan(span: ReadableSpan): ReadableSpan {
  const attrs = span.attributes;
  const method = methodName(attrs["http.request.method"] || attrs["http.method"]);
  const route = routeName(
    attrs["http.route"] || attrs["http.target"] || attrs["http.url"] || attrs["url.full"],
  );
  const status = Number(attrs["http.response.status_code"] || attrs["http.status_code"]);
  return {
    ...span,
    name: `${method} ${route}`,
    attributes: {
      "http.request.method": method,
      "http.route": route,
      ...(status >= 100 && status <= 599 ? { "http.response.status_code": status } : {}),
    },
    events: [],
    links: [],
    status: { code: span.status.code },
    spanContext: () => span.spanContext(),
  };
}
export class SafeExporter implements SpanExporter {
  constructor(private readonly delegate: SpanExporter) {}
  export(spans: ReadableSpan[], callback: Parameters<SpanExporter["export"]>[1]) {
    this.delegate.export(spans.map(sanitizeSpan), callback);
  }
  shutdown() {
    return this.delegate.shutdown();
  }
  forceFlush() {
    return this.delegate.forceFlush?.() ?? Promise.resolve();
  }
}
let registered = false;
export async function registerTelemetry(app: "web" | "admin") {
  if (registered || process.env.DEVFEED_METRICS_ENABLED !== "true") return;
  registered = true;
  const port = Number(process.env.DEVFEED_METRICS_PORT || "9100");
  if (
    !Number.isInteger(port) ||
    port < 1024 ||
    port > 65535 ||
    port === Number(process.env.PORT || "3000")
  )
    throw new Error("Telemetry requires a separate unprivileged port");
  const service = app;
  const registry = new Registry();
  registry.setDefaultLabels({ service });
  collectDefaultMetrics({ register: registry });
  new Gauge({
    name: "devfeed_build_info",
    help: "Running application build",
    labelNames: ["version", "environment"],
    registers: [registry],
  })
    .labels(
      process.env.DEVFEED_VERSION || "development",
      process.env.DEVFEED_TELEMETRY_ENVIRONMENT || "development",
    )
    .set(1);
  const deliveries = new Counter({
    name: "devfeed_faro_deliveries_total",
    help: "Faro receiver outcomes; status 202 means collector accepted",
    labelNames: ["status"],
    registers: [registry],
  });
  observeDeliveries((status) => deliveries.labels(String(status)).inc());
  const requests = new Counter({
    name: "devfeed_http_requests_total",
    help: "Completed application HTTP requests",
    labelNames: ["method", "route", "status"],
    registers: [registry],
  });
  const duration = new Histogram({
    name: "devfeed_http_request_duration_seconds",
    help: "Application HTTP response duration",
    labelNames: ["method", "route"],
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
    registers: [registry],
  });
  const active = new Gauge({
    name: "devfeed_http_requests_in_progress",
    help: "In-flight HTTP requests",
    registers: [registry],
  });
  const component = new Gauge({
    name: "devfeed_telemetry_component_up",
    help: "Telemetry SDK initialization status",
    labelNames: ["component"],
    registers: [registry],
  });
  active.set(0);
  const starts = new WeakMap<ServerResponse, { time: number; method: string; route: string }>();
  const start = (message: unknown) => {
    const { request, response } = message as { request: IncomingMessage; response: ServerResponse };
    if (
      request.socket.localPort === port ||
      /^\/(?:_next\/|telemetry\/collect|health(?:\/|$))/.test(request.url || "")
    )
      return;
    const state = {
      time: performance.now(),
      method: methodName(request.method),
      route: routeName(request.url),
    };
    starts.set(response, state);
    active.inc();
    let finished = false;
    const record = (status: number) => {
      if (finished) return;
      finished = true;
      active.dec();
      requests.labels(state.method, state.route, String(status)).inc();
      duration.labels(state.method, state.route).observe((performance.now() - state.time) / 1000);
      starts.delete(response);
      const span = trace.getActiveSpan()?.spanContext();
      console.info(
        JSON.stringify({
          event: "http_request_completed",
          level: status >= 500 ? "error" : "info",
          service,
          method: state.method,
          route: state.route,
          status_code: status,
          duration_ms: Math.round(performance.now() - state.time),
          ...(span ? { trace_id: span.traceId, span_id: span.spanId } : {}),
        }),
      );
    };
    response.once("finish", () => record(response.statusCode));
    response.once("close", () => record(response.writableFinished ? response.statusCode : 499));
  };
  channel("http.server.request.start").subscribe(start);
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/metrics") {
      try {
        response.writeHead(200, { "Content-Type": registry.contentType });
        response.end(await registry.metrics());
      } catch {
        response.writeHead(500);
        response.end();
      }
      return;
    }
    // Maps live outside Next's public/static tree. Collector access is internal only.
    const map = request.url?.match(
      /^\/sourcemaps\/(?:_next\/static\/)?([a-zA-Z0-9_./-]+\.js\.map)$/,
    );
    if (request.method === "GET" && map && !map[1].includes("..")) {
      const filename = path.join(process.cwd(), ".next/faro-sourcemaps", map[1]);
      try {
        if ((await stat(filename)).isFile()) {
          response.writeHead(200, {
            "Content-Type": "application/json",
            "Cache-Control": "private, max-age=3600",
          });
          createReadStream(filename)
            .on("error", () => response.destroy())
            .pipe(response);
          return;
        }
      } catch {
        /* Missing maps are normal during a rolling release. */
      }
    }
    response.writeHead(404);
    response.end();
  });
  server.requestTimeout = 5000;
  server.headersTimeout = 5000;
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, process.env.DEVFEED_METRICS_HOST || "127.0.0.1", resolve);
  });
  server.unref();
  let sdk: { shutdown(): Promise<void> } | undefined;
  if (process.env.DEVFEED_OTLP_ENDPOINT) {
    try {
      const [
        { NodeSDK },
        { OTLPTraceExporter },
        { resourceFromAttributes },
        { W3CTraceContextPropagator },
        { BatchSpanProcessor, ParentBasedSampler, TraceIdRatioBasedSampler },
      ] = await Promise.all([
        import("@opentelemetry/sdk-node"),
        import("@opentelemetry/exporter-trace-otlp-http"),
        import("@opentelemetry/resources"),
        import("@opentelemetry/core"),
        import("@opentelemetry/sdk-trace-base"),
      ]);
      const instance = new NodeSDK({
        resource: resourceFromAttributes({
          "service.name": `devfeed-${app}`,
          "service.version": process.env.DEVFEED_VERSION || "development",
          "deployment.environment.name": process.env.DEVFEED_TELEMETRY_ENVIRONMENT || "development",
        }),
        autoDetectResources: false,
        textMapPropagator: new W3CTraceContextPropagator(),
        sampler: new ParentBasedSampler({ root: new TraceIdRatioBasedSampler(0.1) }),
        spanProcessors: [
          new BatchSpanProcessor(
            new SafeExporter(
              new OTLPTraceExporter({
                url: `${process.env.DEVFEED_OTLP_ENDPOINT.replace(/\/$/, "")}/v1/traces`,
                timeoutMillis: 1000,
              }),
            ),
            {
              maxQueueSize: 512,
              maxExportBatchSize: 64,
              scheduledDelayMillis: 1000,
              exportTimeoutMillis: 2000,
            },
          ),
        ],
      });
      instance.start();
      sdk = instance;
      component.labels("tracing").set(1);
    } catch {
      component.labels("tracing").set(0);
      console.error(
        JSON.stringify({
          event: "telemetry_init_failed",
          service,
          level: "error",
          component: "tracing",
        }),
      );
    }
  }
  let profiler: { stop(): Promise<void> } | undefined;
  if (process.env.DEVFEED_PYROSCOPE_SERVER) {
    try {
      const pyroscope = (await import("@pyroscope/nodejs")).default;
      pyroscope.setLogger({
        trace() {},
        fatal() {},
        debug() {},
        info() {},
        warn() {},
        error() {
          console.error(
            JSON.stringify({ event: "profile_export_failed", service, level: "error" }),
          );
        },
      });
      pyroscope.init({
        appName: `devfeed-${app}`,
        serverAddress: process.env.DEVFEED_PYROSCOPE_SERVER,
        flushIntervalMs: 60_000,
        wall: { samplingIntervalMicros: 52_632, collectCpuTime: true },
        heap: { samplingIntervalBytes: 1_048_576, stackDepth: 64 },
        tags: {
          environment: process.env.DEVFEED_TELEMETRY_ENVIRONMENT || "development",
          version: process.env.DEVFEED_VERSION || "development",
        },
      });
      pyroscope.start();
      profiler = pyroscope;
      component.labels("profiling").set(1);
    } catch {
      component.labels("profiling").set(0);
      console.error(
        JSON.stringify({
          event: "telemetry_init_failed",
          service,
          level: "error",
          component: "profiling",
        }),
      );
    }
  }
  // Next owns process signals. beforeExit is best effort; periodic batches bound loss at SIGTERM.
  process.once("beforeExit", () => {
    channel("http.server.request.start").unsubscribe(start);
    server.close();
    void Promise.race([
      Promise.allSettled([sdk?.shutdown(), profiler?.stop()]),
      new Promise((resolve) => {
        setTimeout(resolve, 2000).unref();
      }),
    ]);
  });
}

import { trace } from "@opentelemetry/api";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { once } from "node:events";
import { registerTelemetry } from "@devfeed/telemetry/server";

describe("operational telemetry", () => {
  it("serves metrics only on the distinct listener and records real HTTP status", async () => {
    const reservation = createServer();
    reservation.listen(0, "127.0.0.1");
    await once(reservation, "listening");
    const port = (reservation.address() as { port: number }).port;
    await new Promise<void>((resolve) => reservation.close(() => resolve()));
    vi.stubEnv("DEVFEED_BUILD_VERSION", "9.8.7");
    vi.stubEnv("DEVFEED_VERSION", "0.0.8");
    vi.stubEnv("DEVFEED_METRICS_ENABLED", "true");
    vi.stubEnv("DEVFEED_METRICS_PORT", String(port));
    vi.stubEnv("DEVFEED_METRICS_HOST", "127.0.0.1");
    vi.stubEnv("DEVFEED_OTLP_ENDPOINT", "");
    vi.stubEnv("DEVFEED_PYROSCOPE_SERVER", "");
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const app = createServer((request, response) => {
      if (request.url === "/api/v1/user/notifications/chimely/v1/inbox/stream?unauthorized=1") {
        response.statusCode = 401;
        response.end();
        return;
      }
      if (request.url?.includes("/stream") || request.url === "/search?sse=1") {
        response.setHeader("Content-Type", "text/event-stream; charset=utf-8");
        response.write("data: ready\n\n");
        if (!request.url.includes("abort")) response.end();
        return;
      }
      if (request.url === "/search?html=1") {
        response.setHeader("Content-Type", "text/html");
        response.write("<p>chunked</p>");
        response.end();
        return;
      }
      if (request.url === "/search?error=1") {
        response.statusCode = 500;
        response.end();
        return;
      }
      response.statusCode = request.url === "/" ? 200 : 404;
      response.end();
    });
    try {
      await registerTelemetry("web");
      app.listen(0, "127.0.0.1");
      await once(app, "listening");
      const appPort = (app.address() as { port: number }).port;
      expect((await fetch(`http://127.0.0.1:${appPort}/metrics`)).status).toBe(404);
      expect((await fetch(`http://127.0.0.1:${appPort}/`)).status).toBe(200);
      expect((await fetch(`http://127.0.0.1:${appPort}/search?error=1`)).status).toBe(500);
      expect(
        (
          await fetch(
            `http://127.0.0.1:${appPort}/api/v1/user/notifications/chimely/v1/inbox/stream?unauthorized=1`,
          )
        ).status,
      ).toBe(401);
      for (const path of [
        "/api/v1/user/notifications/chimely/v1/inbox/stream?session=private",
        "/v1/admin/notifications/chimely/v1/inbox/stream/",
        "/search?sse=1",
        "/search?html=1",
      ]) {
        await (await fetch(`http://127.0.0.1:${appPort}${path}`)).text();
      }
      const abort = new AbortController();
      const streaming = await fetch(`http://127.0.0.1:${appPort}/search/stream?abort=1`, {
        signal: abort.signal,
      });
      await streaming.body!.getReader().read();
      abort.abort();
      await vi.waitFor(() => {
        expect(log.mock.calls.map(([line]) => JSON.parse(line))).toContainEqual(
          expect.objectContaining({ route: "/search/stream?abort=1", status_code: 499 }),
        );
      });
      await fetch(`http://127.0.0.1:${appPort}/articles/item-123?token=original-value`);
      expect(log.mock.calls.map(([line]) => JSON.parse(line))).toContainEqual(
        expect.objectContaining({ route: "/articles/item-123?token=[REDACTED]" }),
      );
      await fetch(
        `http://127.0.0.1:${appPort}/api/v1/user/auth/callback?code=private-code&state=private-state&limit=25`,
      );
      expect(log.mock.calls.map(([line]) => JSON.parse(line))).toContainEqual(
        expect.objectContaining({
          route: "/api/v1/user/auth/callback?code=[REDACTED]&state=[REDACTED]&limit=25",
        }),
      );
      expect(JSON.stringify(log.mock.calls)).not.toMatch(
        /original-value|private-code|private-state/,
      );
      expect((await fetch(`http://127.0.0.1:${port}/`)).status).toBe(404);
      const response = await fetch(`http://127.0.0.1:${port}/metrics`);
      const metrics = await response.text();
      expect(metrics).toContain("nodejs_heap_size_used_bytes");
      expect(metrics).toContain('version="9.8.7"');
      expect(metrics).not.toContain('version="0.0.8"');
      expect(metrics).toContain('route="/",status="200",service="web"} 1');
      const histogram = metrics
        .split("\n")
        .filter((line) => line.startsWith("http_server_request_duration_seconds_count{"));
      const series = histogram.map((line) => ({
        ...Object.fromEntries(
          [...line.matchAll(/([a-z_]+)="([^"]*)"/g)].map((match) => [match[1], match[2]]),
        ),
        count: Number(line.slice(line.lastIndexOf(" ") + 1)),
      }));
      for (const [route, status, kind] of [
        ["/", "200", "request"],
        ["/search", "500", "request"],
        ["/search", "200", "request"],
        ["/v1/user/notifications/chimely/v1/inbox/stream", "200", "stream"],
        ["/v1/user/notifications/chimely/v1/inbox/stream", "401", "stream"],
        ["unmatched", "200", "stream"],
        ["/search", "200", "stream"],
        ["unmatched", "499", "stream"],
      ]) {
        expect(series).toContainEqual({
          http_request_method: "GET",
          http_route: route,
          http_response_status_code: status,
          devfeed_request_kind: kind,
          service: "web",
          count: 1,
        });
      }
      expect(metrics).not.toContain("devfeed_http_request_duration_seconds");
      expect(histogram.join("\n")).not.toMatch(/private|session=|abort=/);
      expect(metrics).toContain('devfeed_http_requests_in_progress{service="web"} 0');
    } finally {
      app.close();
      log.mockRestore();
      vi.unstubAllEnvs();
    }
  });
});

it("propagates only the active W3C span into internal API requests", () => {
  const span = trace.wrapSpanContext({
    traceId: "a".repeat(32),
    spanId: "b".repeat(16),
    traceFlags: 1,
  });
  const active = vi.spyOn(trace, "getActiveSpan").mockReturnValue(span);
  try {
    expect(traceHeaders()).toEqual({ traceparent: `00-${"a".repeat(32)}-${"b".repeat(16)}-01` });
    active.mockReturnValue(undefined);
    expect(traceHeaders()).toEqual({});
  } finally {
    active.mockRestore();
  }
});

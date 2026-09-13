import { describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { once } from "node:events";
import { routeName, sanitizeBody, sanitizePayload } from "@devfeed/telemetry/privacy";
import { receiveTelemetry } from "@devfeed/telemetry/receiver";
import { registerTelemetry } from "@devfeed/telemetry/server";
const settings = { enabled: true, app: "web" as const, version: "test", environment: "test" };

describe("operational telemetry privacy", () => {
  it("bounds unknown routes, slugs, query strings and dynamic API paths", () => {
    const routes = new Set(
      Array.from({ length: 1000 }, (_, i) => routeName(`/articles/private-${i}?token=secret`)),
    );
    expect([...routes]).toEqual(["/articles/:id"]);
    expect(routeName("/unknown-private-email@example.com")).toBe("unmatched");
    expect(routeName("/api/v1/admin/workers/private-host-name")).toBe("/v1/admin/workers/:path");
  });
  it("drops all user content, exception messages, console logs and arbitrary metadata", () => {
    const result = sanitizeBody(
      {
        meta: {
          app: { name: "spoofed" },
          user: { email: "secret" },
          page: { url: "https://example.com/search?q=secret" },
          session: { id: "secret" },
        },
        exceptions: [
          {
            type: "secret",
            value: "secret",
            context: { secret: "secret" },
            stacktrace: {
              frames: [
                {
                  filename: "https://example.com/_next/static/chunks/abc.js?secret",
                  function: "secret",
                  lineno: 2,
                },
                { filename: "secret" },
              ],
            },
          },
        ],
        logs: [{ message: "secret" }],
        measurements: [{ type: "web-vitals", values: { lcp: 10, secret: 100 } }],
        events: [{ name: "secret" }],
      },
      settings,
    );
    expect(JSON.stringify(result)).not.toContain("secret");
    expect(result.meta.app.name).toBe("devfeed-web");
    expect(result.exceptions).toHaveLength(1);
    expect(result.measurements).toHaveLength(1);
    expect(result.events).toHaveLength(0);
  });
  it("preserves trace correlation and timing while removing payload-bearing OTLP fields", () => {
    const result = sanitizePayload("trace", {
      resourceSpans: [
        {
          resource: { attributes: [{ key: "secret", value: { stringValue: "secret" } }] },
          scopeSpans: [
            {
              spans: [
                {
                  traceId: "a".repeat(32),
                  spanId: "b".repeat(16),
                  startTimeUnixNano: "1000000000",
                  endTimeUnixNano: "2000000000",
                  name: "secret",
                  attributes: [
                    {
                      key: "http.url",
                      value: { stringValue: "https://example.com/search?q=secret" },
                    },
                  ],
                  events: [{ name: "secret" }],
                  status: { code: 2, message: "secret" },
                },
              ],
            },
          ],
        },
      ],
    });
    expect(JSON.stringify(result)).not.toContain("secret");
    expect(JSON.stringify(result)).toContain("a".repeat(32));
    expect(JSON.stringify(result)).toContain("/search");
    const body = sanitizeBody({ traces: result }, settings);
    expect(JSON.stringify(body)).toContain("devfeed-web-browser");
    expect(JSON.stringify(body)).not.toContain("secret");
  });
  it("checks exact origin and body size and only forwards filtered JSON with a server key", async () => {
    vi.stubEnv("DEVFEED_FARO_ENABLED", "true");
    vi.stubEnv("DEVFEED_FARO_COLLECTOR_URL", "http://collector.invalid/collect");
    vi.stubEnv("DEVFEED_FARO_API_KEY", "server-key");
    vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
    const upstream = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
    vi.stubGlobal("fetch", upstream);
    const request = (body: string, origin = "https://devfeed.tech") =>
      new Request("https://devfeed.tech/telemetry/collect", {
        method: "POST",
        headers: { Origin: origin, "Content-Type": "application/json", Cookie: "secret-cookie" },
        body,
      });
    try {
      expect((await receiveTelemetry(request("{}", "https://evil.invalid"), "web")).status).toBe(
        403,
      );
      expect((await receiveTelemetry(request("x".repeat(65537)), "web")).status).toBe(413);
      expect(upstream).not.toHaveBeenCalled();
      expect(
        (
          await receiveTelemetry(
            request(
              JSON.stringify({
                meta: { user: { email: "secret" } },
                logs: [{ message: "secret" }],
              }),
            ),
            "web",
          )
        ).status,
      ).toBe(202);
      const options = upstream.mock.calls[0][1];
      expect(options.headers["X-API-Key"]).toBe("server-key");
      expect(JSON.stringify(options)).not.toContain("secret");
      upstream.mockRejectedValueOnce(new Error("private-collector-error"));
      expect((await receiveTelemetry(request("{}"), "web")).status).toBe(503);
    } finally {
      vi.unstubAllGlobals();
      vi.unstubAllEnvs();
    }
  });
  it("serves metrics only on the distinct listener and records real HTTP status", async () => {
    const reservation = createServer();
    reservation.listen(0, "127.0.0.1");
    await once(reservation, "listening");
    const port = (reservation.address() as { port: number }).port;
    await new Promise<void>((resolve) => reservation.close(() => resolve()));
    vi.stubEnv("DEVFEED_METRICS_ENABLED", "true");
    vi.stubEnv("DEVFEED_METRICS_PORT", String(port));
    vi.stubEnv("DEVFEED_METRICS_HOST", "127.0.0.1");
    vi.stubEnv("DEVFEED_OTLP_ENDPOINT", "");
    vi.stubEnv("DEVFEED_PYROSCOPE_SERVER", "");
    const app = createServer((request, response) => {
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
      expect((await fetch(`http://127.0.0.1:${port}/`)).status).toBe(404);
      const response = await fetch(`http://127.0.0.1:${port}/metrics`);
      const metrics = await response.text();
      expect(metrics).toContain("nodejs_heap_size_used_bytes");
      expect(metrics).toContain('route="/",status="200",service="web"} 1');
      expect(metrics).toContain('devfeed_http_requests_in_progress{service="web"} 0');
    } finally {
      app.close();
      vi.unstubAllEnvs();
    }
  });
});

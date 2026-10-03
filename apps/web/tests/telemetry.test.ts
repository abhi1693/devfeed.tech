import { trace } from "@opentelemetry/api";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { describe, expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { once } from "node:events";
import {
  routeName,
  normalizeBody,
  normalizePayload,
  redactAuthText,
} from "@devfeed/telemetry/privacy";
import redactionCases from "../../../tests/fixtures/auth_redaction_cases.json";
import { receiveTelemetry } from "@devfeed/telemetry/receiver";
import { registerTelemetry } from "@devfeed/telemetry/server";
const settings = { enabled: true, app: "web" as const, version: "test", environment: "test" };

describe("operational telemetry", () => {
  it.each(redactionCases)("redacts authentication in $name", ({ input, expected }) => {
    expect(redactAuthText(input)).toBe(expected);
    expect(redactAuthText(expected)).toBe(expected);
  });
  it("redacts nested Faro URLs, credentials and OTLP attributes while preserving correlation", () => {
    const callback =
      "https://devfeed.tech/auth/callback?code=private-code&state=private-state&q=react";
    const input = {
      meta: { page: { url: callback }, session: { id: "session-123" } },
      exceptions: [
        { value: `GET ${callback} failed`, stacktrace: { frames: [{ filename: callback }] } },
      ],
      events: [{ name: "route_change", attributes: { url: callback, token: "private-token" } }],
      logs: [{ message: callback }],
      measurements: [{ type: "web-vitals", values: { lcp: 123 } }],
      traces: {
        resourceSpans: [
          {
            scopeSpans: [
              {
                spans: [
                  {
                    traceId: "a".repeat(32),
                    spanId: "b".repeat(16),
                    status: { code: 2 },
                    attributes: [
                      { key: "http.url", value: { stringValue: callback } },
                      {
                        key: "url.query",
                        value: { stringValue: "code=private-code&state=private-state" },
                      },
                      {
                        key: "http.request.header.authorization",
                        value: { stringValue: "Bearer private-token" },
                      },
                    ],
                  },
                ],
              },
            ],
          },
        ],
      },
    };
    const result = normalizeBody(input, settings);
    expect(JSON.stringify(result)).not.toMatch(/private-code|private-state|private-token/);
    expect(result.meta).toMatchObject({
      page: { url: callback.replaceAll(/private-(?:code|state)/g, "[REDACTED]") },
      session: { id: "session-123" },
    });
    expect(result.measurements[0]).toMatchObject({ values: { lcp: 123 } });
    expect(result.traces).toMatchObject({
      resourceSpans: [
        {
          scopeSpans: [
            {
              spans: [
                {
                  traceId: "a".repeat(32),
                  spanId: "b".repeat(16),
                  status: { code: 2 },
                  attributes: [
                    { key: "http.url" },
                    { key: "url.query" },
                    {
                      key: "http.request.header.authorization",
                      value: { stringValue: "[REDACTED]" },
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    });
    expect(JSON.stringify(input)).toContain("private-code");
  });
  it("bounds unknown routes, slugs, query strings and dynamic API paths", () => {
    const routes = new Set(
      Array.from({ length: 1000 }, (_, i) => routeName(`/articles/private-${i}?token=secret`)),
    );
    expect([...routes]).toEqual(["/articles/:id"]);
    expect(routeName("/unknown-private-email@example.com")).toBe("unmatched");
    expect(routeName("/api/v1/admin/workers/private-host-name")).toBe("/v1/admin/workers/:path");
  });
  it("bounds metric routes while preserving event URLs", () => {
    expect(routeName("/content/articles/private-id?token=secret")).toBe("/content/articles/:id");
    expect(routeName("/taxonomy/topics/proposals/private-id")).toBe(
      "/taxonomy/topics/proposals/:id",
    );
    expect(routeName("/content/sources/import")).toBe("/content/sources/import");
    expect(routeName("/taxonomy/topics/private-id/private-action")).toBe("unmatched");
    const payload = normalizePayload("event", {
      name: "route_change",
      attributes: { url: "/content/articles/private-id" },
    });
    expect(normalizePayload("event", payload)).toMatchObject({
      attributes: { url: "/content/articles/private-id" },
    });
  });
  it("preserves diagnostics and metadata except authentication fields", () => {
    const result = normalizeBody(
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
    expect(JSON.stringify(result)).toContain("secret");
    expect(result.meta.app.name).toBe("devfeed-web");
    expect(result.exceptions).toHaveLength(1);
    expect(result.measurements).toHaveLength(1);
    expect(result.events).toHaveLength(1);
    expect(result.logs).toEqual([{ message: "secret" }]);
    expect(result.meta).toMatchObject({
      user: { email: "secret" },
      page: { url: "https://example.com/search?q=secret" },
    });
    expect(result.exceptions[0]).toMatchObject({
      type: "secret",
      value: "secret",
      context: { secret: "[REDACTED]" },
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
    });
    expect(JSON.stringify(result)).not.toContain("omitted");
  });
  it("preserves trace correlation, timing and original OTLP fields", () => {
    const result = normalizePayload("trace", {
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
    expect(JSON.stringify(result)).toContain("secret");
    expect(JSON.stringify(result)).toContain("a".repeat(32));
    expect(JSON.stringify(result)).toContain("/search");
    const body = normalizeBody({ traces: result }, settings);
    expect(JSON.stringify(body)).toContain("devfeed-web-browser");
    expect(JSON.stringify(body)).toContain("secret");
  });
  it.each(["web", "admin"] as const)(
    "checks origin and size and preserves %s log content",
    async (app) => {
      vi.stubEnv("DEVFEED_FARO_ENABLED", "true");
      vi.stubEnv("DEVFEED_FARO_COLLECTOR_URL", "http://collector.invalid/collect");
      vi.stubEnv("DEVFEED_FARO_API_KEY", "server-key");
      vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
      vi.stubEnv("DEVFEED_ADMIN_BASE_URL", "https://devfeed.tech");
      const upstream = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
      vi.stubGlobal("fetch", upstream);
      const request = (body: string, origin = "https://devfeed.tech") =>
        new Request("https://devfeed.tech/telemetry/collect", {
          method: "POST",
          headers: { Origin: origin, "Content-Type": "application/json", Cookie: "secret-cookie" },
          body,
        });
      try {
        expect((await receiveTelemetry(request("{}", "https://evil.invalid"), app)).status).toBe(
          403,
        );
        expect((await receiveTelemetry(request("x".repeat(65537)), app)).status).toBe(413);
        expect(upstream).not.toHaveBeenCalled();
        expect(
          (
            await receiveTelemetry(
              request(
                JSON.stringify({
                  meta: {
                    user: { email: "secret" },
                    page: { url: "/auth/callback?code=private-code&state=private-state" },
                  },
                  logs: [
                    { message: "secret" },
                    { message: "/auth/callback?access_token=private-token" },
                  ],
                }),
              ),
              app,
            )
          ).status,
        ).toBe(202);
        const options = upstream.mock.calls[0][1];
        expect(options.headers["X-API-Key"]).toBe("server-key");
        expect(JSON.parse(options.body).logs).toEqual([
          { message: "secret" },
          { message: "/auth/callback?access_token=[REDACTED]" },
        ]);
        expect(options.body).not.toMatch(/private-code|private-state|private-token/);
        expect(JSON.stringify(options.headers)).not.toContain("secret-cookie");
        upstream.mockRejectedValueOnce(new Error("private-collector-error"));
        expect((await receiveTelemetry(request("{}"), app)).status).toBe(503);
      } finally {
        vi.unstubAllGlobals();
        vi.unstubAllEnvs();
      }
    },
  );
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

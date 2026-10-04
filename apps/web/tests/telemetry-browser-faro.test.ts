// @vitest-environment jsdom
import { expect, it } from "vitest";
import {
  BaseTransport,
  initializeFaro,
  SessionInstrumentation,
  type TransportItem,
} from "@grafana/faro-web-sdk";
import { normalizeMeta, normalizePayload } from "@devfeed/telemetry/privacy";

it("telemetry preserves Faro sampling and diagnostics while removing authentication values", () => {
  const delivered: TransportItem[] = [];
  class Capture extends BaseTransport {
    name = "test-capture";
    version = "1";
    send(item: TransportItem | TransportItem[]) {
      delivered.push(...(Array.isArray(item) ? item : [item]));
    }
    getIgnoreUrls() {
      return [];
    }
  }
  const settings = { enabled: true, app: "web" as const, version: "test", environment: "test" };
  const faro = initializeFaro({
    app: { name: "devfeed-web" },
    isolate: true,
    preventGlobalExposure: true,
    transports: [new Capture()],
    instrumentations: [new SessionInstrumentation()],
    batching: { enabled: false },
    sessionTracking: {
      enabled: true,
      persistent: false,
      samplingRate: 1,
      generateSessionId: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    },
    beforeSend(item) {
      const payload = normalizePayload(item.type, item.payload);
      return payload
        ? ({ ...item, meta: normalizeMeta(item.meta, settings, true), payload } as typeof item)
        : null;
    },
  });
  faro.api.setUser({ email: "private-email", id: "private-user" });
  faro.api.pushEvent("telemetry_ready", { url: "/search?q=original-query" });
  faro.api.pushError(new Error("original-error-message"));
  faro.api.pushEvent("route_change", {
    url: "/api/v1/user/auth/callback?code=private-code&state=private-state&limit=25",
  });
  faro.api.pushError(new Error("GET /auth/callback?access_token=private-token failed"));
  expect(
    delivered.some(
      (item) =>
        item.type === "event" && "name" in item.payload && item.payload.name === "telemetry_ready",
    ),
  ).toBe(true);
  expect(JSON.stringify(delivered)).toContain("private-email");
  expect(JSON.stringify(delivered)).toContain("original-error-message");
  expect(JSON.stringify(delivered)).toContain("/search?q=original-query");
  expect(JSON.stringify(delivered)).not.toMatch(/private-code|private-state|private-token/);
  expect(JSON.stringify(delivered)).toContain("code=[REDACTED]&state=[REDACTED]&limit=25");
  expect(JSON.stringify(delivered)).not.toContain("isSampled");
  faro.instrumentations.remove(...faro.instrumentations.instrumentations);
});

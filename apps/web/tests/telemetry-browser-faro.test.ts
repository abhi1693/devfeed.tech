// @vitest-environment jsdom
import { expect, it } from "vitest";
import {
  BaseTransport,
  initializeFaro,
  SessionInstrumentation,
  type TransportItem,
} from "@grafana/faro-web-sdk";
import { sanitizeMeta, sanitizePayload } from "@devfeed/telemetry/privacy";

it("privacy filtering preserves Faro's sampling contract and delivers an anonymous event", () => {
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
      const payload = sanitizePayload(item.type, item.payload);
      return payload
        ? ({ ...item, meta: sanitizeMeta(item.meta, settings, true), payload } as typeof item)
        : null;
    },
  });
  faro.api.setUser({ email: "private-email", id: "private-user" });
  faro.api.pushEvent("telemetry_ready");
  expect(
    delivered.some(
      (item) =>
        item.type === "event" && "name" in item.payload && item.payload.name === "telemetry_ready",
    ),
  ).toBe(true);
  expect(JSON.stringify(delivered)).not.toContain("private-");
  expect(JSON.stringify(delivered)).not.toContain("isSampled");
  faro.instrumentations.remove(...faro.instrumentations.instrumentations);
});

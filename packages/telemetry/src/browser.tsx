"use client";
import { useEffect } from "react";
import { sanitizeMeta, sanitizePayload, type BrowserSettings } from "./privacy";

let initialized = false;
export function BrowserTelemetry(settings: BrowserSettings) {
  useEffect(() => {
    if (!settings.enabled || initialized || navigator.doNotTrack === "1") return;
    initialized = true;
    void Promise.all([import("@grafana/faro-web-sdk"), import("@grafana/faro-web-tracing")])
      .then(([sdk, tracing]) => {
        const collector = `${location.origin}/telemetry/collect`;
        const faro = sdk.initializeFaro({
          app: {
            name: `devfeed-${settings.app}`,
            version: settings.version,
            environment: settings.environment,
          },
          url: collector,
          requestCompression: false,
          preventGlobalExposure: true,
          sessionTracking: {
            enabled: true,
            persistent: false,
            samplingRate: 0.1,
            generateSessionId: () => crypto.randomUUID(),
          },
          batching: { enabled: true, sendTimeout: 5000, itemLimit: 20 },
          ignoreUrls: [/\/telemetry\/collect/],
          trackResources: false,
          instrumentations: [
            ...sdk.getWebInstrumentations({
              captureConsole: false,
              enablePerformanceInstrumentation: false,
            }),
            new tracing.TracingInstrumentation({
              instrumentationOptions: {
                propagateTraceHeaderCorsUrls: [
                  new RegExp(`^${location.origin.replace(/[^a-zA-Z0-9]/g, "\\$&")}/api/`),
                ],
              },
              omitTraceContextForUnsampledSessions: true,
            }),
          ],
          beforeSend(item) {
            const payload = sanitizePayload(item.type, item.payload);
            return payload
              ? ({ ...item, meta: sanitizeMeta(item.meta, settings, true), payload } as typeof item)
              : null;
          },
        });
        faro.api.pushEvent("telemetry_ready");
      })
      .catch(() => {
        initialized = false;
      });
  }, [settings.enabled, settings.app, settings.version, settings.environment]);
  return null;
}

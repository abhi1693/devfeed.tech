import type { Faro } from "@grafana/faro-web-sdk";
import { routeName, normalizeMeta, normalizePayload, type BrowserSettings } from "./privacy";
import { generateSessionId } from "./session";

export async function initializeBrowserTelemetry(settings: BrowserSettings): Promise<Faro> {
  return import("@grafana/faro-web-sdk").then((sdk) => {
    const collector = `${location.origin}/telemetry/collect`;
    let tracingStarted = false;
    const enableTracing = () => {
      if (tracingStarted) return;
      tracingStarted = true;
      void import("@grafana/faro-web-tracing")
        .then((tracing) => {
          faro.instrumentations.add(
            new tracing.TracingInstrumentation({
              instrumentationOptions: {
                propagateTraceHeaderCorsUrls: [
                  new RegExp(`^${location.origin.replace(/[^a-zA-Z0-9]/g, "\\$&")}/api/`),
                ],
              },
              omitTraceContextForUnsampledSessions: true,
            }),
          );
        })
        .catch(() => {
          tracingStarted = false;
        });
    };
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
        generateSessionId,
        onSessionChange(_previous, session) {
          if (session.attributes?.isSampled === "true") enableTracing();
        },
      },
      batching: { enabled: true, sendTimeout: 5000, itemLimit: 20 },
      ignoreUrls: [/\/telemetry\/collect/],
      trackResources: false,
      instrumentations: [
        ...sdk.getWebInstrumentations({
          captureConsole: false,
          enablePerformanceInstrumentation: false,
        }),
      ],
      beforeSend(item) {
        const payload = normalizePayload(item.type, item.payload, settings);
        return payload
          ? ({
              ...item,
              meta: normalizeMeta(item.meta, settings, true),
              payload,
            } as typeof item)
          : null;
      },
    });
    if (faro.api.getSession()?.attributes?.isSampled === "true") enableTracing();
    faro.api.setView({ name: routeName(location.pathname) });
    faro.api.pushEvent("telemetry_ready");
    return faro;
  });
}

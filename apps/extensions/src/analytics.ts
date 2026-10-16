import {
  startReaderAcquisition,
  acquisitionCampaigns,
  readerMeasurementPermitted,
} from "../../web/src/lib/reader-acquisition";
import { analyticsEventName } from "../../web/src/lib/analytics";
import {
  extensionEvent,
  extensionPage,
  type ExtensionEvent,
} from "../../web/src/lib/extension-analytics";
import { version } from "../chrome/manifest.json";
import { publicOrigin } from "./transport";

declare const __DEVFEED_BROWSER__: "chrome" | "edge";
const clientPlatform =
  typeof __DEVFEED_BROWSER__ !== "undefined" && __DEVFEED_BROWSER__ === "edge"
    ? "edge_extension"
    : "chrome_extension";

const endpoint = `${publicOrigin}/api/v1/extension/analytics`;
const identityKey = "devfeed:extension-analytics-client";
const sessionKey = "devfeed:extension-analytics-session";
const timeout = 30 * 60 * 1000;

function context() {
  let locale = "und";
  let timezone = "UTC";
  try {
    locale = navigator.language || locale;
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || timezone;
  } catch {
    /* Keep analytics context coarse and optional. */
  }
  return {
    extension_surface: "newtab",
    locale,
    timezone,
    viewport_width: Math.max(0, Math.min(10000, Math.round(window.innerWidth || 0))),
    viewport_height: Math.max(0, Math.min(10000, Math.round(window.innerHeight || 0))),
  };
}

async function session() {
  return navigator.locks.request("devfeed:extension-analytics", () => {
    let client = localStorage.getItem(identityKey);
    if (!client || !/^\d{1,10}\.\d{1,10}$/.test(client)) {
      client = `${crypto.getRandomValues(new Uint32Array(1))[0]}.${Math.floor(Date.now() / 1000)}`;
      localStorage.setItem(identityKey, client);
    }
    const now = Date.now();
    let previous;
    try {
      previous = JSON.parse(localStorage.getItem(sessionKey) ?? "null");
    } catch {
      /* Start a fresh session. */
    }
    const id =
      previous &&
      Number.isSafeInteger(previous.id) &&
      previous.id > 0 &&
      typeof previous.at === "number" &&
      previous.at <= now &&
      now - previous.at < timeout
        ? previous.id
        : now;
    localStorage.setItem(sessionKey, JSON.stringify({ id, at: now }));
    return { client_id: client, session_id: id };
  });
}

export function startExtensionAnalytics(network: typeof fetch = fetch) {
  const acquisitionEntry = new URL(window.location.hash.slice(1) || "/", publicOrigin);
  let stopAcquisition = () => {};
  const disableAcquisition = () => {
    stopAcquisition();
    stopAcquisition = startReaderAcquisition({
      enabled: false,
      platform: clientPlatform,
      url: acquisitionEntry,
    });
  };
  const privacy = navigator as Navigator & { globalPrivacyControl?: boolean };
  try {
    if (
      navigator.doNotTrack === "1" ||
      privacy.globalPrivacyControl ||
      localStorage.getItem("devfeed:extension-analytics-disabled") === "true"
    ) {
      disableAcquisition();
      return stopAcquisition;
    }
  } catch {
    disableAcquisition();
    return stopAcquisition;
  }
  let enabled = false;
  let stopped = false;
  let pending = 0;
  let route = window.location.hash.slice(1).split("?")[0] || "/";
  let activeSince: number | null = null;
  let viewed = false;
  const isActive = () => document.visibilityState === "visible" && document.hasFocus();
  const takeTime = () => {
    const now = performance.now();
    const duration = activeSince === null ? 0 : now - activeSince;
    activeSince = isActive() ? now : null;
    return Math.min(timeout, Math.max(0, Math.floor(duration)));
  };
  const send = (event: ExtensionEvent, engagement = takeTime()) => {
    if (!enabled || stopped || !readerMeasurementPermitted() || pending >= 10) return;
    pending++;
    void session()
      .then((identity) => {
        if (stopped) return;
        return network(endpoint, {
          method: "POST",
          credentials: "omit",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...identity,
            event,
            ...context(),
            engagement_time_msec: engagement,
            extension_version: version,
            client_platform: clientPlatform,
          }),
          keepalive: true,
          redirect: "error",
          referrerPolicy: "no-referrer",
          signal: AbortSignal.timeout(5000),
        });
      })
      .catch(() => {
        /* Analytics must never interrupt the reader or retry indefinitely. */
      })
      .finally(() => {
        pending--;
      });
  };
  const engagement = () => {
    const time = takeTime();
    if (viewed && time > 0)
      send({ name: "user_engagement", params: { page_path: extensionPage(route) } }, time);
  };
  const pageView = () => {
    if (!enabled || !isActive()) return;
    viewed = true;
    activeSince = performance.now();
    send({ name: "page_view", params: { page_path: extensionPage(route) } }, 0);
  };
  const navigate = () => {
    const next = window.location.hash.slice(1).split("?")[0] || "/";
    if (next === route) return;
    engagement();
    route = next;
    viewed = false;
    pageView();
  };
  const activity = () => {
    if (!enabled) return;
    if (!isActive()) {
      engagement();
      activeSince = null;
    } else if (activeSince === null) {
      activeSince = performance.now();
      if (!viewed) pageView();
    }
  };
  const event = (value: Event) => {
    if (!isActive()) return;
    const sanitized = extensionEvent((value as CustomEvent).detail);
    if (sanitized) send(sanitized);
  };
  const controller = new AbortController();
  void network(endpoint, {
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
    signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
  })
    .then(async (response) =>
      response.ok
        ? ((await response.json()) as {
            enabled?: boolean;
            reader_measurement_enabled?: boolean;
            reader_acquisition_campaigns?: unknown;
          })
        : null,
    )
    .then((value) => {
      if (!stopped && value?.enabled === true && readerMeasurementPermitted()) {
        enabled = true;
        if (value.reader_measurement_enabled === true) {
          stopAcquisition();
          stopAcquisition = startReaderAcquisition({
            enabled: true,
            campaigns: acquisitionCampaigns(value.reader_acquisition_campaigns),
            platform: clientPlatform,
            url: acquisitionEntry,
            referrer: "",
            currentIdentity: () => {
              if (!enabled || stopped || !readerMeasurementPermitted()) return null;
              try {
                const client = localStorage.getItem(identityKey);
                const previous = JSON.parse(localStorage.getItem(sessionKey) ?? "null");
                if (
                  client &&
                  /^\d{1,10}\.\d{1,10}$/.test(client) &&
                  previous &&
                  Number.isSafeInteger(previous.id) &&
                  previous.id > 0 &&
                  typeof previous.at === "number" &&
                  previous.at <= Date.now() &&
                  Date.now() - previous.at < timeout
                )
                  return { client_id: client, session_id: previous.id };
              } catch {
                /* Existing analytics storage is optional. */
              }
              return null;
            },
          });
        }
        if (value.reader_measurement_enabled !== true) disableAcquisition();
        pageView();
      } else if (!stopped) disableAcquisition();
    })
    .catch(() => {});
  window.addEventListener(analyticsEventName, event);
  window.addEventListener("devfeed:extension-route", navigate);
  window.addEventListener("hashchange", navigate);
  window.addEventListener("focus", activity);
  window.addEventListener("blur", activity);
  window.addEventListener("pagehide", engagement);
  document.addEventListener("visibilitychange", activity);
  const timer = window.setInterval(() => {
    if (enabled && isActive()) engagement();
  }, 60000);
  return () => {
    stopped = true;
    stopAcquisition();
    controller.abort();
    window.clearInterval(timer);
    window.removeEventListener(analyticsEventName, event);
    window.removeEventListener("devfeed:extension-route", navigate);
    window.removeEventListener("hashchange", navigate);
    window.removeEventListener("focus", activity);
    window.removeEventListener("blur", activity);
    window.removeEventListener("pagehide", engagement);
    document.removeEventListener("visibilitychange", activity);
  };
}

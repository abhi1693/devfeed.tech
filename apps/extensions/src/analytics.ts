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
  const privacy = navigator as Navigator & { globalPrivacyControl?: boolean };
  try {
    if (
      navigator.doNotTrack === "1" ||
      privacy.globalPrivacyControl ||
      localStorage.getItem("devfeed:extension-analytics-disabled") === "true"
    )
      return () => {};
  } catch {
    return () => {};
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
    if (!enabled || stopped || pending >= 10) return;
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
    .then(async (response) => response.ok && (await response.json()).enabled === true)
    .then((value) => {
      if (!stopped && value) {
        enabled = true;
        pageView();
      }
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

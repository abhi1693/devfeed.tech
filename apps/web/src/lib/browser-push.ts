const workerPath = "/web-push-sw.js";

export type BrowserPushConfig = {
  enabled: boolean;
  public_key: string | null;
  delivery_hour: number;
  delivery_timezone?: string | null;
};
export type BrowserPushSubscription = {
  id: string;
  consent_id: string;
  endpoint_hash: string;
  enabled: boolean;
  timezone: string;
};

export function browserPushAvailability(): "supported" | "unsupported" | "home-screen" {
  const ios =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (
    ios &&
    !(navigator as Navigator & { standalone?: boolean }).standalone &&
    !window.matchMedia("(display-mode: standalone)").matches
  )
    return "home-screen";
  return window.isSecureContext &&
    "Notification" in window &&
    "serviceWorker" in navigator &&
    "PushManager" in window
    ? "supported"
    : "unsupported";
}

export function browserPushTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export async function pushEndpointHash(endpoint: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function pushApplicationKey(publicKey: string) {
  const decoded = atob(publicKey.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}

export async function browserPushRegistration() {
  if (!("serviceWorker" in navigator)) return undefined;
  const registration = await navigator.serviceWorker.getRegistration("/");
  const worker = registration?.active ?? registration?.waiting ?? registration?.installing;
  return worker?.scriptURL === new URL(workerPath, window.location.origin).href
    ? registration
    : undefined;
}

export async function registerBrowserPush() {
  const registration = await navigator.serviceWorker.register(workerPath, {
    scope: "/",
    updateViaCache: "none",
  });
  if (registration.active) return registration;
  const worker = registration.installing ?? registration.waiting;
  if (!worker) throw new Error("Notification worker unavailable");
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      worker.removeEventListener("statechange", changed);
      reject(new Error("Notification worker did not start"));
    }, 15000);
    function changed() {
      if (worker!.state !== "activated" && worker!.state !== "redundant") return;
      window.clearTimeout(timer);
      worker!.removeEventListener("statechange", changed);
      if (worker!.state === "activated") resolve();
      else reject(new Error("Notification worker unavailable"));
    }
    worker.addEventListener("statechange", changed);
    changed();
  });
  return registration;
}

async function workerRequest(
  registration: ServiceWorkerRegistration,
  message: { type: string; subscription_id?: string | null; account_id?: string | null },
) {
  const worker = registration.active;
  if (!worker) throw new Error("Notification worker unavailable");
  return new Promise<{ revoked?: boolean }>((resolve, reject) => {
    const channel = new MessageChannel();
    const timer = window.setTimeout(() => {
      channel.port1.close();
      reject(new Error("Notification consent could not be saved"));
    }, 10000);
    channel.port1.onmessage = (event) => {
      window.clearTimeout(timer);
      channel.port1.close();
      if (event.data?.ok) resolve(event.data);
      else reject(new Error("Notification consent could not be saved"));
    };
    worker.postMessage(message, [channel.port2]);
  });
}

export async function setBrowserPushConsent(
  registration: ServiceWorkerRegistration,
  subscriptionId: string | null,
  accountId: string | null = null,
) {
  if (subscriptionId && !accountId) throw new Error("Notification account is required");
  await workerRequest(registration, {
    type: "devfeed:push-consent",
    subscription_id: subscriptionId,
    account_id: accountId,
  });
}

/** Reconcile account changes even when the reader never visits notification settings. */
export async function reconcileBrowserPushAccount(accountId: string) {
  const registration = await browserPushRegistration();
  if (!registration) return;
  const result = await workerRequest(registration, {
    type: "devfeed:push-account",
    account_id: accountId,
  });
  if (result.revoked) {
    const subscription = await registration.pushManager.getSubscription();
    await subscription?.unsubscribe();
  }
}

/** Clear local consent before unsubscribe so queued pushes cannot reveal a previous account. */
export async function clearBrowserPush() {
  const registration = await browserPushRegistration();
  if (!registration) return;
  try {
    await setBrowserPushConsent(registration, null);
  } finally {
    const subscription = await registration.pushManager.getSubscription();
    await subscription?.unsubscribe();
  }
}

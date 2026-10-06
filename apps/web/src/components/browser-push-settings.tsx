"use client";

import { useEffect, useState } from "react";
import { userRequest } from "@/lib/user";
import { readerIsExtension, readerWebsiteLink } from "@/lib/reader-runtime";
import {
  browserPushAvailability,
  browserPushRegistration,
  browserPushTimezone,
  clearBrowserPush,
  pushApplicationKey,
  pushEndpointHash,
  registerBrowserPush,
  setBrowserPushConsent,
  type BrowserPushConfig,
  type BrowserPushSubscription,
} from "@/lib/browser-push";
import { useUser } from "./user-account";

type PushState = {
  availability: ReturnType<typeof browserPushAvailability>;
  config?: BrowserPushConfig;
  subscription?: BrowserPushSubscription;
  permission?: NotificationPermission;
  failed?: boolean;
};

export function BrowserPushSettings() {
  const { user } = useUser();
  const [state, setState] = useState<PushState>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const extension = readerIsExtension();

  useEffect(() => {
    if (!user || extension) return;
    const availability = browserPushAvailability();
    const controller = new AbortController();
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]);
    void (async () => {
      if (availability !== "supported") {
        setState({ availability });
        return;
      }
      const [config, saved, registration] = await Promise.all([
        userRequest<BrowserPushConfig>("notifications/push/config", { signal }),
        userRequest<{ subscriptions: BrowserPushSubscription[] }>(
          "notifications/push/subscriptions",
          { signal },
        ),
        browserPushRegistration(),
      ]);
      const browserSubscription = await registration?.pushManager.getSubscription();
      const hash = browserSubscription && (await pushEndpointHash(browserSubscription.endpoint));
      const subscription = saved.subscriptions.find((item) => item.endpoint_hash === hash);
      if (controller.signal.aborted) return;
      if (registration)
        await setBrowserPushConsent(
          registration,
          config.enabled && subscription?.enabled && Notification.permission === "granted"
            ? subscription.consent_id
            : null,
          user.user_id,
        );
      if (!controller.signal.aborted)
        setState({ availability, config, subscription, permission: Notification.permission });
    })().catch(() => {
      if (!controller.signal.aborted)
        setState({ availability, permission: Notification.permission, failed: true });
    });
    return () => controller.abort();
  }, [user, extension, revision]);

  async function enable() {
    if (!user || busy || !state?.config?.public_key) return;
    // Safari requires requestPermission to happen directly inside the click gesture.
    let permissionPromise: Promise<NotificationPermission>;
    try {
      permissionPromise = Notification.requestPermission();
    } catch {
      setError(true);
      setMessage("Couldn’t request notification permission. Check your browser’s site settings.");
      return;
    }
    setBusy(true);
    setMessage("");
    setError(false);
    let registration: ServiceWorkerRegistration | undefined;
    let browserSubscription: PushSubscription | null = null;
    let saved: { id: string; consent_id: string; enabled: boolean; timezone: string } | undefined;
    try {
      const permission = await permissionPromise;
      setState((previous) => previous && { ...previous, permission });
      if (permission !== "granted") {
        setMessage(
          permission === "denied"
            ? "Notifications are blocked. Allow them in your browser’s site settings to enable your daily must-read."
            : "Notifications were not enabled. You can try again whenever you’re ready.",
        );
        return;
      }
      registration = await registerBrowserPush();
      browserSubscription = await registration.pushManager.getSubscription();
      const applicationServerKey = pushApplicationKey(state.config.public_key);
      const existingKey = browserSubscription?.options.applicationServerKey;
      if (
        browserSubscription &&
        existingKey &&
        (existingKey.byteLength !== applicationServerKey.length ||
          !new Uint8Array(existingKey).every((byte, index) => byte === applicationServerKey[index]))
      ) {
        await browserSubscription.unsubscribe();
        browserSubscription = null;
      }
      browserSubscription ??= await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
      const { endpoint, keys } = browserSubscription.toJSON();
      if (!endpoint || !keys?.auth || !keys?.p256dh) throw new Error("Incomplete subscription");
      const timezone = browserPushTimezone();
      saved = await userRequest("notifications/push/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify({ endpoint, keys, timezone }),
      });
      if (!saved?.enabled) throw new Error("Notification subscription unavailable");
      await setBrowserPushConsent(registration, saved.consent_id, user.user_id);
      setState(
        (previous) =>
          previous && {
            ...previous,
            subscription: { ...saved!, endpoint_hash: "" },
            permission: "granted",
          },
      );
      setMessage("Daily must-read notifications are enabled on this browser.");
    } catch {
      if (saved)
        await userRequest(`notifications/push/subscriptions/${saved.id}`, {
          method: "DELETE",
          headers: { "X-CSRF-Token": user.csrf_token },
        }).catch(() => {});
      if (registration) await setBrowserPushConsent(registration, null).catch(() => {});
      await browserSubscription?.unsubscribe().catch(() => {});
      setError(true);
      setMessage("Couldn’t enable daily must-read notifications. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    if (!user || !state?.subscription || busy) return;
    setBusy(true);
    setMessage("");
    setError(false);
    try {
      await userRequest(`notifications/push/subscriptions/${state.subscription.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": user.csrf_token },
      });
      // The server has stopped delivery even if the browser is unable to unsubscribe.
      await clearBrowserPush().catch(() => {});
      setState((previous) => previous && { ...previous, subscription: undefined });
      setMessage("Daily must-read notifications are disabled on this browser.");
    } catch {
      setError(true);
      setMessage("Couldn’t disable daily must-read notifications. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const enabled = state?.subscription?.enabled;
  const deliveryTime = new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(
    new Date(2000, 0, 1, state?.config?.delivery_hour ?? 9),
  );
  const deliveryTimezone = state?.config
    ? (state.config.delivery_timezone ?? browserPushTimezone())
    : null;
  return (
    <section
      className="profile-panel browser-push-settings"
      aria-label="Daily must-read browser notifications"
    >
      <h2>Browser notifications</h2>
      <p className="profile-description">
        One personalized must-read article each day. Delivered around {deliveryTime}
        {deliveryTimezone ? ` in ${deliveryTimezone}` : " each morning"}, when there’s an unread
        article for you. No other browser alerts.
      </p>
      {extension ? (
        <a className="settings-button" {...readerWebsiteLink("/settings/notifications")}>
          Manage browser notifications
        </a>
      ) : !state ? (
        <p className="settings-help" role="status">
          Checking this browser…
        </p>
      ) : state.availability === "home-screen" ? (
        <p className="settings-help" role="status">
          On iPhone or iPad, add DevFeed to your Home Screen from the browser’s share menu. Open
          DevFeed from that icon, then enable your daily must-read here.
        </p>
      ) : state.availability === "unsupported" ? (
        <p className="settings-help" role="status">
          This browser does not support push notifications. Try a supported browser with a secure
          connection to DevFeed.
        </p>
      ) : state.failed ? (
        <div className="notification-connection" role="status">
          <p>Couldn’t check browser notification settings.</p>
          <button
            className="settings-button settings-button-ghost"
            onClick={() => setRevision((value) => value + 1)}
          >
            Retry browser connection
          </button>
        </div>
      ) : enabled ? (
        <>
          <p className="settings-help">
            {state.permission === "denied"
              ? "Notifications are blocked in your browser’s site settings."
              : !state.config?.enabled
                ? "Daily must-read delivery is currently unavailable."
                : "Enabled on this browser"}
            {" · "}
            {state.subscription!.timezone}
          </p>
          <button
            className="settings-button settings-button-ghost"
            disabled={busy}
            aria-busy={busy}
            onClick={disable}
          >
            {busy ? "Disabling…" : "Disable notifications on this browser"}
          </button>
        </>
      ) : !state.config?.enabled || !state.config.public_key ? (
        <p className="settings-help" role="status">
          Daily must-read browser notifications are currently unavailable.
        </p>
      ) : state.permission === "denied" ? (
        <p className="settings-help" role="status">
          Notifications are blocked. Allow them in your browser’s site settings, then reload this
          page to enable your daily must-read.
        </p>
      ) : (
        <button className="settings-button" disabled={busy} aria-busy={busy} onClick={enable}>
          {busy ? "Enabling…" : "Enable notifications on this browser"}
        </button>
      )}
      {message && (
        <p className={`profile-feedback${error ? " error" : ""}`} role={error ? "alert" : "status"}>
          {message}
        </p>
      )}
    </section>
  );
}

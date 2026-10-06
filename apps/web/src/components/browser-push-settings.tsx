"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { userRequest, type UserIdentity } from "@/lib/user";
import { readerIsExtension, readerWebsiteLink } from "@/lib/reader-runtime";
import {
  browserPushAvailability,
  browserPushConsentRevision,
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
  const { user, sessionRevision } = useUser();
  return (
    <BrowserPushSettingsSession
      key={`${user?.user_id}:${user?.csrf_token}:${sessionRevision}`}
      user={user}
    />
  );
}

function BrowserPushSettingsSession({ user }: { user: UserIdentity | null }) {
  const [state, setState] = useState<PushState>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const session = useRef<AbortController | null>(null);
  const enrollment = useRef<AbortController | null>(null);
  const extension = readerIsExtension();

  useLayoutEffect(() => {
    const controller = new AbortController();
    session.current = controller;
    const cancel = () => {
      controller.abort();
      enrollment.current?.abort();
    };
    window.addEventListener("devfeed:user-session-expired", cancel);
    return () => {
      cancel();
      if (session.current === controller) session.current = null;
      window.removeEventListener("devfeed:user-session-expired", cancel);
    };
  }, []);

  useEffect(() => {
    const sessionController = session.current;
    if (!user || extension || !sessionController || sessionController.signal.aborted) return;
    const availability = browserPushAvailability();
    const consentRevision = browserPushConsentRevision();
    const controller = new AbortController();
    const signal = AbortSignal.any([
      controller.signal,
      sessionController.signal,
      AbortSignal.timeout(15000),
    ]);
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
      if (controller.signal.aborted || sessionController.signal.aborted) return;
      if (registration)
        await setBrowserPushConsent(
          registration,
          subscription?.enabled && Notification.permission === "granted"
            ? subscription.consent_id
            : null,
          user.user_id,
          consentRevision,
        );
      if (!controller.signal.aborted && !sessionController.signal.aborted)
        setState({ availability, config, subscription, permission: Notification.permission });
    })().catch(() => {
      if (!controller.signal.aborted && !sessionController.signal.aborted)
        setState({ availability, permission: Notification.permission, failed: true });
    });
    return () => controller.abort();
  }, [user, extension, revision]);

  async function enable() {
    const sessionController = session.current;
    if (
      !user ||
      busy ||
      !sessionController ||
      sessionController.signal.aborted ||
      !state?.config?.public_key
    )
      return;
    setBusy(true);
    setMessage("");
    setError(false);
    const controller = new AbortController();
    const consentRevision = browserPushConsentRevision();
    enrollment.current = controller;
    const ownsEnrollment = () =>
      enrollment.current === controller &&
      !controller.signal.aborted &&
      !sessionController.signal.aborted &&
      consentRevision === browserPushConsentRevision();
    const requestSignal = () =>
      AbortSignal.any([controller.signal, sessionController.signal, AbortSignal.timeout(15000)]);
    let registration: ServiceWorkerRegistration | undefined;
    let browserSubscription: PushSubscription | null = null;
    let saved: { id: string; consent_id: string; enabled: boolean; timezone: string } | undefined;
    let permissionResolved = false;
    try {
      // Safari requires this call inside the click gesture, before the first await.
      const permission = await Notification.requestPermission();
      permissionResolved = true;
      if (!ownsEnrollment()) return;
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
      if (!ownsEnrollment()) return;
      browserSubscription = await registration.pushManager.getSubscription();
      if (!ownsEnrollment()) return;
      const applicationServerKey = pushApplicationKey(state.config.public_key);
      const existingKey = browserSubscription?.options.applicationServerKey;
      if (
        browserSubscription &&
        existingKey &&
        (existingKey.byteLength !== applicationServerKey.length ||
          !new Uint8Array(existingKey).every((byte, index) => byte === applicationServerKey[index]))
      ) {
        await browserSubscription.unsubscribe();
        if (!ownsEnrollment()) return;
        browserSubscription = null;
      }
      browserSubscription ??= await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
      if (!ownsEnrollment()) return;
      const { endpoint, keys } = browserSubscription.toJSON();
      if (!endpoint || !keys?.auth || !keys?.p256dh) throw new Error("Incomplete subscription");
      const timezone = browserPushTimezone();
      saved = await userRequest("notifications/push/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
        body: JSON.stringify({ endpoint, keys, timezone }),
        signal: requestSignal(),
      });
      if (!ownsEnrollment()) return;
      if (!saved?.enabled) throw new Error("Notification subscription unavailable");
      await setBrowserPushConsent(registration, saved.consent_id, user.user_id, consentRevision);
      if (!ownsEnrollment()) return;
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
      // An older session must never undo a newer browser enrollment.
      if (!ownsEnrollment()) return;
      if (!permissionResolved) {
        setError(true);
        setMessage("Couldn’t request notification permission. Check your browser’s site settings.");
        return;
      }
      if (saved)
        await userRequest(`notifications/push/subscriptions/${saved.id}`, {
          method: "DELETE",
          headers: { "X-CSRF-Token": user.csrf_token },
          signal: requestSignal(),
        }).catch(() => {});
      if (!ownsEnrollment()) return;
      if (registration)
        await setBrowserPushConsent(registration, null, null, consentRevision).catch(() => {});
      if (!ownsEnrollment()) return;
      await browserSubscription?.unsubscribe().catch(() => {});
      if (!ownsEnrollment()) return;
      setError(true);
      setMessage("Couldn’t enable daily must-read notifications. Please try again.");
    } finally {
      if (enrollment.current === controller && !sessionController.signal.aborted) {
        enrollment.current = null;
        setBusy(false);
      }
    }
  }

  async function disable() {
    const sessionController = session.current;
    if (!user || !state?.subscription || busy || !sessionController) return;
    const signal = AbortSignal.any([sessionController.signal, AbortSignal.timeout(15000)]);
    setBusy(true);
    setMessage("");
    setError(false);
    try {
      await userRequest(`notifications/push/subscriptions/${state.subscription.id}`, {
        method: "DELETE",
        headers: { "X-CSRF-Token": user.csrf_token },
        signal,
      });
      if (sessionController.signal.aborted) return;
      // The server has stopped delivery even if the browser is unable to unsubscribe.
      await clearBrowserPush().catch(() => {});
      if (sessionController.signal.aborted) return;
      setState((previous) => previous && { ...previous, subscription: undefined });
      setMessage("Daily must-read notifications are disabled on this browser.");
    } catch {
      if (sessionController.signal.aborted) return;
      setError(true);
      setMessage("Couldn’t disable daily must-read notifications. Please try again.");
    } finally {
      if (!sessionController.signal.aborted) setBusy(false);
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

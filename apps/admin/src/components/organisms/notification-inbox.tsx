"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ChimelyProvider, InboxContent, useChimelyClient, useNotifications, useUnseenCount, type InboxAppearance } from "@chimely/react";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { NotificationBell } from "@/components/molecules/notification-bell";
import { adminNotificationConfig } from "@/lib/api/generated/admin";
import type { NotificationConfig } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { createInboxClient, inboxAction } from "@/lib/inbox";

import { useSettings } from "@/lib/use-settings";
import { useNotificationSound } from "@/lib/use-notification-sound";
import { notificationCategoryLabels, preferencesChanged, inheritNotificationPreferences } from "@/lib/notification-preferences";

const appearance: InboxAppearance = {
  variables: { colorPrimary: "var(--foreground)", colorPrimaryHover: "var(--muted)", colorBackground: "var(--card)", colorForeground: "var(--card-foreground)", colorMuted: "var(--border)", colorBadge: "var(--primary)", colorBadgeForeground: "var(--primary-foreground)", fontFamily: "inherit", fontSize: "14px", borderRadius: "6px" },
  classNames: { content: "devfeed-inbox", item: "devfeed-notification", footer: "devfeed-inbox-footer" },
};

export function NotificationInbox({ csrfToken }: { csrfToken: string }) {
  const [config, setConfig] = useState<NotificationConfig>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void adminNotificationConfig({ signal: abort.signal }).then(value => {
      if (!abort.signal.aborted) { setConfig(value); setError(false); }
    }).catch(cause => {
      if (abort.signal.aborted) return;
      if (cause instanceof ApiError && [401, 403].includes(cause.status)) { returnToLogin(); return; }
      setError(true);
    });
    return () => abort.abort();
  }, [retry]);
  if (config?.enabled && config.environment && config.subscriber_id) {
    return <ConnectedInbox key={`${config.environment}:${config.subscriber_id}:${csrfToken}`} config={config} csrfToken={csrfToken} />;
  }
  if (!error) return null;
  return <Popover><PopoverTrigger asChild><NotificationBell /></PopoverTrigger>
    <PopoverContent align="end" className="w-80 max-w-[calc(100vw-24px)] p-4" aria-label="Notifications">
      <h2 className="text-sm font-semibold">Notifications</h2>
      <p role="status" className="mt-2 text-sm text-muted-foreground">Notifications are temporarily unavailable.</p>
      <Button className="mt-3" size="sm" variant="outline" onClick={() => setRetry(value => value + 1)}>Retry</Button>
    </PopoverContent></Popover>;
}

function ConnectedInbox({ config, csrfToken }: { config: NotificationConfig; csrfToken: string }) {
  const [client] = useState(() => createInboxClient(config, csrfToken));
  const [preferenceError, setPreferenceError] = useState(false);
  useEffect(() => {
    let disposed = false;
    let ready = false;
    const prepare = async () => {
      try { await inheritNotificationPreferences(client); }
      catch { if (!disposed) setPreferenceError(true); return; }
      if (!disposed) { setPreferenceError(false); ready = true; client.connect(); }
    };
    // Resolve saved category opt-outs before the first event-level inbox read.
    void prepare();
    // Also refetch periodically: Chimely's Redis/SSE hints are optional and a
    // silent hint outage must not leave the visible inbox stale indefinitely.
    const timer = setInterval(() => { if (document.visibilityState === "visible") { if (ready) void client.refresh(); else void prepare(); } }, 60_000);
    const onFocus = () => { if (document.visibilityState === "visible") { if (ready) void client.refresh(); else void prepare(); } };
    document.addEventListener("visibilitychange", onFocus);
    window.addEventListener(preferencesChanged, onFocus);
    return () => { disposed = true; clearInterval(timer); window.removeEventListener(preferencesChanged, onFocus); document.removeEventListener("visibilitychange", onFocus); client.close(); };
  }, [client]);
  return <ChimelyProvider client={client}><InboxPopover preferenceError={preferenceError} /></ChimelyProvider>;
}

function InboxPopover({ preferenceError = false }: { preferenceError?: boolean }) {
  const client = useChimelyClient();
  const { count } = useUnseenCount();
  const { error, isLoading, items } = useNotifications();
  const { settings } = useSettings();
  useNotificationSound(count, isLoading, settings.notifications.sound, Math.max(0, ...items.map(item => Date.parse(item.occurredAt))));
  const [open, setOpen] = useState(false);
  const router = useRouter();
  return <Popover open={open} onOpenChange={value => {
    setOpen(value);
    if (value && !preferenceError) { void client.markAllSeen(); void client.refresh(); }
  }}>
    <PopoverTrigger asChild><NotificationBell count={settings.notifications.show_badge ? count : 0} /></PopoverTrigger>
    <PopoverContent align="end" className="w-[420px] max-w-[calc(100vw-24px)] overflow-hidden p-0" aria-label="Notifications">
      {(error || preferenceError) && <div role="status" className="flex items-center gap-3 border-b p-3 text-sm">
        <AlertTriangle className="size-4 shrink-0 text-amber-600" aria-hidden />
        <span>Notifications unavailable. Retrying automatically.</span>
        <Button variant="outline" size="sm" loading={isLoading} onClick={() => { if (preferenceError) window.dispatchEvent(new Event(preferencesChanged)); else void client.refresh(); }}>Retry</Button>
      </div>}
      <InboxContent appearance={appearance}
        localization={{ emptyTitle: "No notifications", emptyBody: "Background-job updates and important announcements will appear here.", categoryLabels: notificationCategoryLabels }}
        tabs={[{ label: "All" }, { label: "Attention", filter: item => ["warning", "error"].includes(String(item.payload.severity)) }]}
        onItemClick={item => {
          const href = inboxAction(item.payload.action_url);
          void client.markRead(item);
          if (href) { setOpen(false); router.push(href); }
          return false;
        }}
        renderAvatar={({ item }) => {
          const severity = item.payload.severity;
          const Icon = severity === "error" ? XCircle : severity === "warning" ? AlertTriangle : severity === "success" ? CheckCircle2 : Info;
          return <Icon aria-label={String(severity ?? "info")} className={`size-5 ${severity === "error" ? "text-destructive" : severity === "warning" ? "text-amber-600" : severity === "success" ? "text-emerald-700" : "text-muted-foreground"}`} />;
        }}
        renderFooter={() => <Button variant="link" size="sm" asChild><Link href="/jobs/notifications" onClick={() => setOpen(false)}>Notification delivery logs</Link></Button>}
      />
    </PopoverContent>
  </Popover>;
}

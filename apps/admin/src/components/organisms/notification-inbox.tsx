"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { NotificationInbox as SharedInbox, NotificationsUnavailable } from "@devfeed/ui/notifications";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import { adminNotificationConfig } from "@/lib/api/generated/admin";
import type { NotificationConfig } from "@/lib/api/generated/models";
import { ApiError, returnToLogin } from "@/lib/api/client";
import { createInboxClient, inboxAction } from "@/lib/inbox";
import { useSettings } from "@/lib/use-settings";
import { notificationCategoryLabels, inheritNotificationPreferences } from "@/lib/notification-preferences";

export function NotificationInbox({ csrfToken }: { csrfToken: string }) {
  const [config, setConfig] = useState<NotificationConfig>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let complete = false;
    return runWhenPageActive(signal => {
      if (complete) return;
      void adminNotificationConfig({ signal }).then(value => {
        if (!signal.aborted) { setConfig(value); setError(false); }
      }).catch(cause => {
        if (signal.aborted) return;
        if (cause instanceof ApiError && [401, 403].includes(cause.status)) { returnToLogin(); return; }
        setError(true);
      }).finally(() => { if (!signal.aborted) complete = true; });
    });
  }, [retry]);
  if (config?.enabled && config.environment && config.subscriber_id) {
    return <ConnectedInbox key={`${config.environment}:${config.subscriber_id}:${csrfToken}`} config={config} csrfToken={csrfToken} />;
  }
  if (!error) return null;
  return <NotificationsUnavailable onRetry={() => setRetry(value => value + 1)} />;
}

function ConnectedInbox({ config, csrfToken }: { config: NotificationConfig; csrfToken: string }) {
  const [client] = useState(() => createInboxClient(config, csrfToken));
  const { settings } = useSettings();
  const router = useRouter();
  return <SharedInbox client={client} prepare={inheritNotificationPreferences}
    showBadge={settings.notifications.show_badge} sound={settings.notifications.sound}
    emptyBody="Background-job updates and important announcements will appear here."
    categoryLabels={notificationCategoryLabels}
    tabs={[{ label: "All" }, { label: "Attention", filter: item => ["warning", "error"].includes(String(item.payload.severity)) }]}
    actionUrl={inboxAction} onNavigate={href => router.push(href)}
    footer={{ href: "/jobs/notifications", label: "Notification delivery logs" }} />;
}

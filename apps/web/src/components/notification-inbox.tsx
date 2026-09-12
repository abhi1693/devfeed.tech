"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { NotificationInbox as SharedInbox, NotificationsUnavailable } from "@devfeed/ui/notifications";
import { runWhenPageActive } from "@devfeed/ui/page-activity";
import { useNotificationPreferences, userNotificationLabels } from "./notification-preferences-provider";
import { useUser } from "./user-account";
import { userRequest, type UserIdentity } from "@/lib/user";
import { createInboxClient, notificationArticle, type InboxConfig } from "@/lib/inbox";

export function NotificationInbox() {
  const { user } = useUser();
  return user ? <UserInbox key={user.user_id} user={user} /> : null;
}

function UserInbox({ user }: { user: UserIdentity }) {
  const [config, setConfig] = useState<InboxConfig>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let complete = false;
    return runWhenPageActive(signal => {
      if (complete) return;
      void userRequest<InboxConfig>("notifications/config", {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
      }).then(value => {
        if (!signal.aborted) { setConfig(value); setError(false); }
      }).catch(() => {
        if (!signal.aborted) setError(true);
      }).finally(() => { if (!signal.aborted) complete = true; });
    });
  }, [retry]);
  if (config?.enabled && config.environment && config.subscriber_id)
    return (
      <ConnectedInbox
        key={user.csrf_token}
        config={config}
        csrf={user.csrf_token}
      />
    );
  if (error) return <NotificationsUnavailable onRetry={() => setRetry(value => value + 1)} />;
  return null;
}

function ConnectedInbox({ config, csrf }: { config: InboxConfig; csrf: string }) {
  const [client] = useState(() => createInboxClient(config, csrf));
  const router = useRouter();
  const { value } = useNotificationPreferences();
  return <SharedInbox client={client} showBadge={value?.show_badge ?? true} sound={value?.sound ?? false}
    emptyBody="New articles from topics and sources you follow will appear here."
    categoryLabels={userNotificationLabels}
    tabs={[{ label: "All" }, { label: "Unread", filter: item => !item.read }]}
    actionUrl={notificationArticle} onNavigate={href => router.push(href, { scroll: !href.startsWith("/articles/") })}
    footer={{ href: "/settings/notifications", label: "Notification settings" }} />;
}

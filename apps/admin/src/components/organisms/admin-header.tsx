import type { AdminIdentity } from "@/lib/api/generated/models";
import { Brand } from "@/components/molecules/brand";
import { SignOut } from "@/components/molecules/sign-out";
import { SessionLifetime } from "@/components/molecules/session-lifetime";
import { NotificationInbox } from "@/components/organisms/notification-inbox";

export function AdminHeader({ admin }: { admin: AdminIdentity }) {
  return <header className="border-b bg-card">
    <SessionLifetime expiresAt={admin.expires_at} />
    <div className="flex flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
      <Brand />
      <div className="flex min-w-0 items-center gap-4">
        <span className="max-w-48 truncate text-sm text-muted-foreground" title={admin.email ?? admin.subject}>
          {admin.name || admin.email || admin.subject}
        </span>
        <NotificationInbox csrfToken={admin.csrf_token} />
        <SignOut csrfToken={admin.csrf_token} />
      </div>
    </div>
  </header>;
}

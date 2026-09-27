import { ChimelyClient } from "@chimely/client";
import { createInboxFetch } from "@devfeed/ui/inbox-fetch";
import type { NotificationConfig } from "@/lib/api/generated/models";
import { returnToLogin } from "@/lib/api/client";
import { canonicalAdminRedirect } from "@/lib/routes";

export const inboxPath = "/api/v1/admin/notifications/chimely";

/** Subscriber identities are bound by the API, never trusted from these props. */
export function createInboxClient(config: NotificationConfig, csrfToken: string) {
  return new ChimelyClient({
    serverUrl: inboxPath,
    environment: config.environment!,
    subscriberId: config.subscriber_id!,
    fetchFn: createInboxFetch({
      origin: () => window.location.origin,
      path: inboxPath,
      csrfToken,
      request: async (url, init) => {
        const response = await fetch(url, init);
        if (response.status === 401 || response.status === 403) returnToLogin();
        return response;
      },
    }),
  });
}

/** Announcements cannot turn inbox clicks into script or external navigation. */
export function inboxAction(value: unknown): string | null {
  if (typeof value !== "string" || !/^\/[a-zA-Z0-9/_-]*$/.test(value) || value.startsWith("//"))
    return null;
  return canonicalAdminRedirect(value.slice(1).split("/")) ?? value;
}

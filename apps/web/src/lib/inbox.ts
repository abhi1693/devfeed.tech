import { ChimelyClient } from "@chimely/client";
import { createInboxFetch } from "@devfeed/ui/inbox-fetch";
import { readerPublicOrigin, readerRequest } from "./reader-runtime";

export type InboxConfig = {
  enabled: boolean;
  environment: string | null;
  subscriber_id: string | null;
};
export const inboxPath = "/api/v1/user/notifications/chimely";

export function createInboxClient(config: InboxConfig, csrf: string) {
  return new ChimelyClient({
    serverUrl: new URL(inboxPath, readerPublicOrigin()).href,
    createEventSource: (url) => new EventSource(url, { withCredentials: true }),
    environment: config.environment!,
    subscriberId: config.subscriber_id!,
    fetchFn: createInboxFetch({
      origin: readerPublicOrigin,
      path: inboxPath,
      csrfToken: csrf,
      request: async (url, init) => {
        const response = await readerRequest(url, init);
        if (response.status === 401)
          window.dispatchEvent(new Event("devfeed:user-session-expired"));
        return response;
      },
    }),
  });
}

export function notificationArticle(value: unknown): string | null {
  return typeof value === "string" && /^\/articles\/[a-z0-9][a-z0-9-]{0,199}$/i.test(value)
    ? value
    : null;
}

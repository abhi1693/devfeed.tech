import { ChimelyClient } from "@chimely/client";
import { isPageActive } from "@devfeed/ui/page-activity";

export type InboxConfig = {
  enabled: boolean;
  environment: string | null;
  subscriber_id: string | null;
};
export const inboxPath = "/api/v1/user/notifications/chimely";

export function createInboxClient(config: InboxConfig, csrf: string) {
  return new ChimelyClient({
    serverUrl: inboxPath,
    environment: config.environment!,
    subscriberId: config.subscriber_id!,
    fetchFn: async (input, init) => {
      const url = new URL(String(input), window.location.origin);
      if (
        url.origin !== window.location.origin ||
        !url.pathname.startsWith(inboxPath + "/v1/inbox/")
      )
        throw new Error("Invalid inbox endpoint");
      const headers = new Headers(init?.headers);
      if (["GET", "HEAD"].includes(init?.method ?? "GET") && !isPageActive())
        throw new DOMException("Page is inactive", "AbortError");
      if (!["GET", "HEAD"].includes(init?.method ?? "GET"))
        headers.set("X-CSRF-Token", csrf);
      const response = await fetch(input, {
        ...init,
        headers,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: init?.signal
          ? AbortSignal.any([init.signal, AbortSignal.timeout(15000)])
          : AbortSignal.timeout(15000),
      });
      if (response.status === 401)
        window.dispatchEvent(new Event("devfeed:user-session-expired"));
      return response;
    },
  });
}

export function notificationArticle(value: unknown): string | null {
  return typeof value === "string" &&
    /^\/articles\/[a-z0-9][a-z0-9-]{0,199}$/i.test(
      value,
    )
    ? value
    : null;
}

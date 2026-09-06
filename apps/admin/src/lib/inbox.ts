import { ChimelyClient } from "@chimely/client";
import type { NotificationConfig } from "@/lib/api/generated/models";
import { returnToLogin } from "@/lib/api/client";

export const inboxPath = "/api/v1/admin/notifications/chimely";

/** Subscriber identities are bound by the API, never trusted from these props. */
export function createInboxClient(config: NotificationConfig, csrfToken: string) {
  return new ChimelyClient({
    serverUrl: inboxPath,
    environment: config.environment!, subscriberId: config.subscriber_id!,
    fetchFn: async (input, init) => {
      const url = new URL(String(input), window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith(inboxPath + "/v1/inbox/")) {
        throw new Error("Invalid inbox endpoint");
      }
      const headers = new Headers(init?.headers);
      if (!["GET", "HEAD"].includes(init?.method ?? "GET")) headers.set("X-CSRF-Token", csrfToken);
      const response = await fetch(input, { ...init, headers, credentials: "same-origin", cache: "no-store", redirect: "error",
        signal: init?.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
      });
      if (response.status === 401 || response.status === 403) returnToLogin();
      return response;
    },
  });
}

/** Announcements cannot turn inbox clicks into script or external navigation. */
export function inboxAction(value: unknown): string | null {
  if (typeof value !== "string" || !/^\/[a-zA-Z0-9/_-]*$/.test(value) || value.startsWith("//")) return null;
  return value;
}

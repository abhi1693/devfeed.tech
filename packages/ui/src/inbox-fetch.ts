import { isPageActive } from "./page-activity";

type InboxFetchOptions = {
  origin: () => string;
  path: string;
  csrfToken: string;
  request: (url: string, init: RequestInit) => Promise<Response>;
};

/** Shared request safeguards; callers own transport and authorization handling. */
export function createInboxFetch({ origin, path, csrfToken, request }: InboxFetchOptions) {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const base = origin();
    const url = new URL(String(input), base);
    if (url.origin !== base || !url.pathname.startsWith(path + "/v1/inbox/"))
      throw new Error("Invalid inbox endpoint");
    const headers = new Headers(init?.headers);
    const read = ["GET", "HEAD"].includes(init?.method ?? "GET");
    if (read && !isPageActive()) throw new DOMException("Page is inactive", "AbortError");
    if (!read) headers.set("X-CSRF-Token", csrfToken);
    return request(url.href, {
      ...init,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: init?.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(15_000)])
        : AbortSignal.timeout(15_000),
    });
  };
}

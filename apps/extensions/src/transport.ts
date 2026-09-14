export const publicOrigin = "https://devfeed.tech";

const publicReads = new Set([
  "/api/v1/feed",
  "/api/v1/feed/options",
  "/api/v1/topics",
  "/api/v1/sources",
  "/api/v1/search",
]);
const userPath = /^\/api\/v1\/user\/[a-zA-Z0-9_/-]+$/;
const methods = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]);

// Chrome supplies the existing HttpOnly website session through host permission.
// Never read/copy cookies or provider tokens into extension storage or headers.
export function createReaderTransport(network: typeof fetch): typeof fetch {
  return async (input, init) => {
    const original = input instanceof Request ? input : undefined;
    const url = new URL(original ? original.url : String(input), publicOrigin);
    if (url.origin !== publicOrigin || url.username || url.password)
      throw new Error("Unexpected reader API origin");
    const method = (init?.method ?? original?.method ?? "GET").toUpperCase();
    const privateApi = userPath.test(url.pathname);
    const articleRead = /^\/api\/v1\/articles\/[a-z0-9][a-z0-9-]{0,199}$/i.test(url.pathname);
    const authPath = url.pathname.startsWith("/api/v1/user/auth/");
    if (
      !methods.has(method) ||
      (!privateApi && !((publicReads.has(url.pathname) || articleRead) && method === "GET")) ||
      (authPath &&
        !["/api/v1/user/auth/me", "/api/v1/user/auth/config", "/api/v1/user/auth/logout"].includes(
          url.pathname,
        ))
    )
      return Response.json({ detail: "Unsupported reader request" }, { status: 403 });
    const supplied = new Headers(init?.headers ?? original?.headers);
    const headers = new Headers({ Accept: "application/json" });
    for (const name of ["Content-Type", "X-CSRF-Token", "If-None-Match"]) {
      const value = supplied.get(name);
      if (value !== null && privateApi) headers.set(name, value);
    }
    const signal = init?.signal ?? original?.signal;
    return network(url.href, {
      method,
      headers,
      cache: "no-store",
      credentials: "include",
      redirect: "error",
      referrerPolicy: "no-referrer",
      keepalive: init?.keepalive,
      body: ["GET", "HEAD"].includes(method)
        ? undefined
        : (init?.body ?? (original ? await original.clone().arrayBuffer() : undefined)),
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(15000)])
        : AbortSignal.timeout(15000),
    });
  };
}

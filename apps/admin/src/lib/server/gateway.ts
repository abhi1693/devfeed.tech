import "server-only";
import { adminApiOrigin, adminWebOrigin } from "./config";

// This is not a general-purpose proxy. Only the admin service's namespace is reachable.
const privatePath = /^\/v1\/admin\/[a-zA-Z0-9_/-]+$/;
const safe = new Set(["GET", "HEAD", "OPTIONS"]);

function upstreamTimeout(method: string, path: string): number {
  // Each guarded download has a 60s deadline, but a final in-flight socket
  // operation can take another 30s. Preview may fetch both the feed and website.
  // Leave 30s for parsing/database work; ordinary calls and SSE stay bounded.
  if (method === "POST") {
    // GitHub discovery fetches the revision and a complete, size-limited archive.
    // Its first request needs time for the download and parsing before batching.
    if (path === "/v1/admin/topic-discovery/github") return 120_000;
    if (path === "/v1/admin/sources") return 120_000;
    if (path === "/v1/admin/sources/preview") return 210_000;
  }
  return 45_000;
}

export async function gateway(request: Request, segments: string[]) {
  const path = "/" + segments.join("/");
  if (!privatePath.test(path) || segments.some(part => part === "." || part === "..")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  try {
    const origin = adminWebOrigin();
    const incoming = new URL(request.url);
    // Host/proxy headers do not determine callback redirects or CSRF trust.
    if (!safe.has(request.method) && request.headers.get("origin") !== origin) {
      return Response.json({ detail: "Invalid request origin" }, { status: 403 });
    }
    const headers = new Headers({ Accept: "application/json" });
    for (const name of ["cookie", "content-type", "origin", "x-csrf-token", "if-none-match", "last-event-id"]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    let body: ArrayBuffer | undefined;
    if (!safe.has(request.method)) {
      if (Number(request.headers.get("content-length")) > 1_000_000) {
        return Response.json({ detail: "Request too large" }, { status: 413 });
      }
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 1_000_000) {
            await reader.cancel();
            return Response.json({ detail: "Request too large" }, { status: 413 });
          }
          chunks.push(value);
        }
      }
      const buffer = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.length; }
      body = buffer.buffer;
    }
    const upstream = await fetch(`${adminApiOrigin()}${path}${incoming.search}`, {
      method: request.method, headers, body, redirect: "manual", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(upstreamTimeout(request.method, path))]),
    });
    const resultHeaders = new Headers({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" });
    for (const name of ["content-type", "location", "x-request-id", "x-devfeed-version", "etag", "x-accel-buffering", "retry-after"]) {
      const value = upstream.headers.get(name);
      if (value) resultHeaders.set(name, value);
    }
    // Never collapse Set-Cookie: callback rotates a session and deletes the state cookie.
    for (const value of upstream.headers.getSetCookie()) resultHeaders.append("Set-Cookie", value);
    return new Response(upstream.body, { status: upstream.status, headers: resultHeaders });
  } catch {
    // Do not log callback URLs, provider messages, codes, cookies, or tokens.
    return Response.json({ detail: "Admin service unavailable" }, {
      status: 503, headers: { "Cache-Control": "no-store" },
    });
  }
}

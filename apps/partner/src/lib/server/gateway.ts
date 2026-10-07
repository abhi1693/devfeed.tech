import { traceHeaders } from "@devfeed/telemetry/propagation";
import "server-only";
import { partnerApiOrigin, partnerWebOrigin } from "./config";

// This is not a general-purpose proxy. Only the partner service's namespace is reachable.
const privatePath = /^\/v1\/partner\/[a-zA-Z0-9_/-]+$/;
const safe = new Set(["GET", "HEAD", "OPTIONS"]);

export async function gateway(request: Request, segments: string[]) {
  const path = "/" + segments.join("/");
  if (!privatePath.test(path) || segments.some((part) => part === "." || part === "..")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  try {
    const origin = partnerWebOrigin();
    const incoming = new URL(request.url);
    // Host/proxy headers do not determine callback redirects or CSRF trust.
    if (!safe.has(request.method) && request.headers.get("origin") !== origin) {
      return Response.json({ detail: "Invalid request origin" }, { status: 403 });
    }
    const headers = new Headers({ Accept: "application/json" });
    for (const name of [
      "cookie",
      "content-type",
      "origin",
      "x-csrf-token",
      "if-none-match",
      "last-event-id",
    ]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    const bodyLimit = 1_000_000;
    let body: ArrayBuffer | undefined;
    if (!safe.has(request.method)) {
      if (Number(request.headers.get("content-length")) > bodyLimit) {
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
          if (size > bodyLimit) {
            await reader.cancel();
            return Response.json({ detail: "Request too large" }, { status: 413 });
          }
          chunks.push(value);
        }
      }
      const buffer = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        buffer.set(chunk, offset);
        offset += chunk.length;
      }
      body = buffer.buffer;
    }
    for (const [key, value] of Object.entries(traceHeaders())) headers.set(key, value);
    const upstream = await fetch(`${partnerApiOrigin()}${path}${incoming.search}`, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(45_000)]),
    });
    const resultHeaders = new Headers({
      "Cache-Control": "no-store",
      "Referrer-Policy": "no-referrer",
    });
    for (const name of [
      "content-type",
      "location",
      "x-request-id",
      "x-devfeed-version",
      "etag",
      "x-accel-buffering",
      "retry-after",
    ]) {
      const value = upstream.headers.get(name);
      if (value) resultHeaders.set(name, value);
    }
    // Never collapse Set-Cookie: callback rotates a session and deletes the state cookie.
    for (const value of upstream.headers.getSetCookie()) resultHeaders.append("Set-Cookie", value);
    return new Response(upstream.body, { status: upstream.status, headers: resultHeaders });
  } catch {
    // Do not log callback URLs, provider messages, codes, cookies, or tokens.
    return Response.json(
      { detail: "Partner service unavailable" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}

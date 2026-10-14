import { traceHeaders } from "@devfeed/telemetry/propagation";
import { GatewayRouter } from "@devfeed/http/gateway";
import {
  readRequestBody,
  RequestBodyTimeout,
  RequestBodyTooLarge,
} from "@devfeed/http/request-body";
import "server-only";
import { adminGatewayOperations } from "../api/generated/gateway-operations";
import { adminApiOrigin, adminWebOrigin } from "./config";

const router = new GatewayRouter({ operations: adminGatewayOperations });
const safe = new Set(["GET", "HEAD", "OPTIONS"]);

export async function gateway(request: Request, segments: string[]) {
  const match = router.resolve(request.method, segments);
  if (match.kind === "method-not-allowed")
    return Response.json(
      { detail: "Method not allowed" },
      { status: 405, headers: { Allow: match.allowedMethods.join(", ") } },
    );
  if (match.kind !== "matched") {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  const { path, operation } = match;
  const { policy } = operation;
  try {
    const origin = adminWebOrigin();
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
    let body: ArrayBuffer | undefined;
    if (!safe.has(request.method)) {
      body = await readRequestBody(request, policy.bodyLimit, policy.bodyTimeoutMs);
    }
    request.signal.throwIfAborted();
    for (const [key, value] of Object.entries(traceHeaders())) headers.set(key, value);
    const upstream = await fetch(`${adminApiOrigin()}${path}${incoming.search}`, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(policy.upstreamTimeoutMs)]),
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
  } catch (error) {
    if (error instanceof RequestBodyTooLarge)
      return Response.json(
        { detail: "Request too large" },
        { status: 413, headers: { "Cache-Control": "no-store" } },
      );
    if (error instanceof RequestBodyTimeout)
      return Response.json(
        { detail: "Incomplete request" },
        { status: 408, headers: { "Cache-Control": "no-store" } },
      );
    // Do not log callback URLs, provider messages, codes, cookies, or tokens.
    return Response.json(
      { detail: "Admin service unavailable" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}

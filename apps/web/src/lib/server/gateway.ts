import { traceHeaders } from "@devfeed/telemetry/propagation";
import "server-only";
import { userApiOrigin, userRequestOriginAllowed, xPixelEnabled } from "./config";
import { readRequestBody, RequestBodyTimeout, RequestBodyTooLarge } from "./request-body";
import { validPlacementQuery } from "../partner-placements";

// This is not a general-purpose proxy. Only the user service's namespace is reachable.
const privatePath = /^\/v1\/user\/[a-zA-Z0-9_/-]+$/;
export function userCookies(value: string, attribution = false) {
  return value
    .split(";")
    .map((part) => part.trim())
    .filter(
      (part) =>
        /^(?:__Host-)?devfeed_user_(?:session|state|visitor)=/.test(part) ||
        (attribution && /^(?:(?:__Host-)?devfeed_user_x_click|_twclid)=/.test(part)),
    )
    .join("; ");
}
const safe = new Set(["GET", "HEAD", "OPTIONS"]);

export async function gateway(request: Request, segments: string[]) {
  const path = "/" + segments.join("/");
  if (!privatePath.test(path) || segments.some((part) => part === "." || part === "..")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  try {
    const incoming = new URL(request.url);
    // Host/proxy headers do not determine callback redirects or CSRF trust.
    if (!safe.has(request.method) && !userRequestOriginAllowed(request.headers.get("origin"))) {
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
      if (value)
        headers.set(
          name,
          name === "cookie"
            ? userCookies(value, path === "/v1/user/auth/login" && xPixelEnabled())
            : value,
        );
    }
    let body: ArrayBuffer | undefined;
    if (!safe.has(request.method)) {
      const placementQuery = path === "/v1/user/partner-placements/query";
      const limit = placementQuery
        ? 8192
        : request.method === "POST" && path === "/v1/user/settings/profile/avatar"
          ? 5 * 1024 * 1024 + 65536
          : 1_000_000;
      body = await readRequestBody(request, limit, placementQuery ? 2000 : undefined);
      if (placementQuery) {
        let query: unknown;
        try {
          query = JSON.parse(new TextDecoder().decode(body));
        } catch {
          query = null;
        }
        if (request.method !== "POST" || !validPlacementQuery(query))
          return Response.json(
            { detail: "Invalid placement context" },
            { status: 422, headers: { "Cache-Control": "no-store" } },
          );
      }
    }
    for (const [key, value] of Object.entries(traceHeaders())) headers.set(key, value);
    const upstream = await fetch(`${userApiOrigin()}${path}${incoming.search}`, {
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
    return new Response(upstream.body, {
      status: upstream.status,
      headers: resultHeaders,
    });
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
      { detail: "User service unavailable" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}

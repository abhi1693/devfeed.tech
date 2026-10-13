import { traceHeaders } from "@devfeed/telemetry/propagation";
import {
  readRequestBody,
  RequestBodyTimeout,
  RequestBodyTooLarge,
} from "@devfeed/http/request-body";
import "server-only";
import { partnerApiOrigin, partnerWebOrigin } from "./config";

// This is not a general-purpose proxy. Only the partner service's namespace is reachable.
const privatePath = /^\/v1\/partner\/[a-zA-Z0-9_/-]+$/;
const safe = new Set(["GET", "HEAD", "OPTIONS"]);
const preferencesPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/preferences$/i;
const destinationPreviewPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/destination-preview$/i;
const audienceTopicsPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/audience-topics$/i;
const creativePreviewPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/creative-preview$/i;
const productPromotionPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/products\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/promotion$/i;
const productAdImagePath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/products\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/ad-image$/i;
const inventoryPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/inventory$/i;
const accountDashboardPath =
  /^\/v1\/partner\/accounts\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/dashboard$/i;
const connectionsPath =
  /^\/v1\/partner\/accounts\/[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\/connections(?:\/([a-z0-9]+(?:-[a-z0-9]+)*))?(?:\/(actions|jobs))?$/;
const connectionPreviewPath =
  /^\/v1\/partner\/accounts\/[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}\/connections(?:\/([a-z0-9]+(?:-[a-z0-9]+)*))?\/(connector-response|connector-preview)$/;
const workspaceConnectionsPath =
  /^\/v1\/partner\/connections(?:\/([a-z0-9]+(?:-[a-z0-9]+)*))?(?:\/(jobs))?$/;

function connectionMethods(path: string): readonly string[] | undefined {
  const preview = connectionPreviewPath.exec(path);
  if (preview) {
    if (preview[1] && (preview[1] === "new" || preview[1].length > 200)) return undefined;
    return ["POST"];
  }
  const workspace = workspaceConnectionsPath.exec(path);
  const match = workspace ?? connectionsPath.exec(path);
  if (!match) return undefined;
  const [, provider, subresource] = match;
  if (provider && (provider === "new" || provider.length > 200)) return undefined;
  if (!provider) return ["GET", "POST"];
  if (workspace) return ["GET"];
  if (subresource === "actions") return ["POST"];
  if (subresource === "jobs") return ["GET"];
  return ["GET", "PUT", "DELETE"];
}

export async function gateway(request: Request, segments: string[]) {
  const path = "/" + segments.join("/");
  if (
    !privatePath.test(path) ||
    segments.some((part) => part === "." || part === ".." || part.includes("/"))
  ) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  const connections = connectionMethods(path);
  if (segments.some((part) => part.toLowerCase() === "connections")) {
    if (!connections) return Response.json({ detail: "Not found" }, { status: 404 });
    if (!connections.includes(request.method))
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("destination-preview")) {
    if (!destinationPreviewPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "GET")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("audience-topics")) {
    if (!audienceTopicsPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "GET")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("creative-preview")) {
    if (!creativePreviewPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "GET")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("promotion")) {
    if (!productPromotionPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (!["GET", "PUT"].includes(request.method))
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.some((part) => part.toLowerCase() === "ad-image")) {
    if (!productAdImagePath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "POST")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("inventory")) {
    if (path !== "/v1/partner/inventory" && !inventoryPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "GET")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (!connections && segments.includes("dashboard")) {
    if (path !== "/v1/partner/dashboard" && !accountDashboardPath.test(path))
      return Response.json({ detail: "Not found" }, { status: 404 });
    if (request.method !== "GET")
      return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }
  if (
    !safe.has(request.method) &&
    !connections?.includes(request.method) &&
    !(request.method === "POST" && path === "/v1/partner/auth/logout") &&
    !(request.method === "POST" && productAdImagePath.test(path)) &&
    !(request.method === "PUT" && (preferencesPath.test(path) || productPromotionPath.test(path)))
  ) {
    return Response.json({ detail: "Method not allowed" }, { status: 405 });
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
    let body: ArrayBuffer | undefined;
    if (!safe.has(request.method)) {
      const imageUpload = productAdImagePath.test(path);
      body = await readRequestBody(
        request,
        imageUpload ? 5 * 1024 * 1024 + 65536 : 1_000_000,
        imageUpload ? 10_000 : 2000,
      );
    }
    request.signal.throwIfAborted();
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
      { detail: "Partner service unavailable" },
      {
        status: 503,
        headers: { "Cache-Control": "no-store" },
      },
    );
  }
}

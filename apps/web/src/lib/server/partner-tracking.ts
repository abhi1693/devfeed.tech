import "server-only";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { publicApiOrigin } from "./config";
import { readRequestBody, RequestBodyTimeout, RequestBodyTooLarge } from "./request-body";

export async function partnerTracking(request: Request) {
  const headers = new Headers({ "Cache-Control": "no-store" });
  try {
    const body = await readRequestBody(request, 2048, 2000);
    const upstream = await fetch(new URL("/v1/partner-tracking/events", publicApiOrigin()), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...traceHeaders() },
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(1500)]),
    });
    await upstream.body?.cancel();
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter !== null) headers.set("Retry-After", retryAfter);
    if (upstream.status >= 500 || (upstream.status >= 300 && upstream.status < 400)) {
      return Response.json({ detail: "Partner tracking unavailable" }, { status: 503, headers });
    }
    return new Response(null, { status: upstream.status, headers });
  } catch (error) {
    if (error instanceof RequestBodyTooLarge)
      return Response.json({ detail: "Request too large" }, { status: 413, headers });
    if (error instanceof RequestBodyTimeout)
      return Response.json({ detail: "Incomplete request" }, { status: 408, headers });
    return Response.json({ detail: "Partner tracking unavailable" }, { status: 503, headers });
  }
}

import "server-only";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { parsePartnerPlacements, validPlacementQuery } from "../partner-placements";
import { publicApiOrigin } from "./config";
import { readRequestBody, RequestBodyTimeout, RequestBodyTooLarge } from "./request-body";

export async function partnerPlacements(request: Request) {
  const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
  try {
    const body = await readRequestBody(request, 8192, 2000);
    let query: unknown;
    try {
      query = JSON.parse(new TextDecoder().decode(body));
    } catch {
      query = null;
    }
    if (!validPlacementQuery(query))
      return Response.json({ detail: "Invalid placement context" }, { status: 422, headers });
    const upstream = await fetch(new URL("/v1/partner-placements/query", publicApiOrigin()), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...traceHeaders() },
      body: JSON.stringify(query),
      credentials: "omit",
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(3000)]),
    });
    if (!upstream.ok) {
      await upstream.body?.cancel();
      return Response.json(
        { detail: "Partner recommendations unavailable" },
        { status: 503, headers },
      );
    }
    const items = parsePartnerPlacements(await upstream.json());
    return Response.json({ items }, { headers });
  } catch (error) {
    if (error instanceof RequestBodyTooLarge)
      return Response.json({ detail: "Request too large" }, { status: 413, headers });
    if (error instanceof RequestBodyTimeout)
      return Response.json({ detail: "Incomplete request" }, { status: 408, headers });
    return Response.json(
      { detail: "Partner recommendations unavailable" },
      { status: 503, headers },
    );
  }
}

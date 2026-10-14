import "server-only";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { parsePartnerPlacements, validPlacementQuery } from "../partner-placements";
import { publicApiOrigin } from "./config";
import { partnerCallerHeaders } from "./partner-caller";
import type { ReaderGatewayOperation } from "@devfeed/http/gateway";
import { bindReaderOperation } from "./gateway";
import {
  readRequestBody,
  RequestBodyTimeout,
  RequestBodyTooLarge,
} from "@devfeed/http/request-body";

function placementQuery(body: ArrayBuffer): unknown {
  try {
    return JSON.parse(new TextDecoder().decode(body));
  } catch {
    return null;
  }
}

export function bindPersonalizedPartnerPlacements(operation: ReaderGatewayOperation) {
  if (operation.method !== "POST" || operation.policy.handler !== "placement-query")
    throw new Error("Personalized placements require a placement-query POST operation");
  return bindReaderOperation(operation, (body) =>
    validPlacementQuery(placementQuery(body))
      ? null
      : Response.json(
          { detail: "Invalid placement context" },
          {
            status: 422,
            headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
          },
        ),
  );
}

export async function partnerPlacements(request: Request) {
  const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
  try {
    const body = await readRequestBody(request, 8192, 2000);
    const query = placementQuery(body);
    if (!validPlacementQuery(query))
      return Response.json({ detail: "Invalid placement context" }, { status: 422, headers });
    const upstream = await fetch(new URL("/v1/partner-placements/query", publicApiOrigin()), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...traceHeaders(),
        ...partnerCallerHeaders(request, "/v1/partner-placements/query"),
      },
      body: JSON.stringify(query),
      credentials: "omit",
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(3000)]),
    });
    if (!upstream.ok) {
      await upstream.body?.cancel();
      const retryAfter = upstream.status === 429 ? upstream.headers.get("retry-after") : null;
      return Response.json(
        { detail: "Partner recommendations unavailable" },
        {
          status: upstream.status === 429 ? 429 : 503,
          headers: retryAfter === null ? headers : { ...headers, "Retry-After": retryAfter },
        },
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

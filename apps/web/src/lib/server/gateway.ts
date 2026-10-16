import "server-only";
import { readerMeasurementHeader, sanitizeReaderMeasurement } from "../reader-acquisition";
import {
  GatewayTransport,
  methodNotAllowed,
  type GatewayBodyValidator,
} from "@devfeed/http/forwarding";
import {
  GatewayRoute,
  type GatewayParameters,
  type ReaderGatewayOperation,
} from "@devfeed/http/gateway";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import {
  userApiOrigin,
  userWebOrigin,
  userRequestOriginAllowed,
  xPixelEnabled,
  readerMeasurementEnabled,
  readerAcquisitionCampaigns,
} from "./config";
import { ReaderCookies } from "./reader-cookies";

const cookies = new ReaderCookies(xPixelEnabled, readerMeasurementEnabled);
const transport = new GatewayTransport<ReaderGatewayOperation>({
  apiOrigin: userApiOrigin,
  webOrigin: userWebOrigin,
  originAllowed: userRequestOriginAllowed,
  filterCookies: (value, operation) =>
    cookies.filter(
      value,
      operation.policy.cookiePolicy,
      operation.operationId === "user_auth_callback",
    ),
  requestHeaders: (request, operation): Record<string, string> => {
    const forwarded: Record<string, string> = {};
    const measurementOperation = [
      "user_auth_login",
      "user_auth_callback",
      "opened_v1_user_articles__article_id__open_post",
    ].includes(operation.operationId);
    if (measurementOperation) {
      for (const name of ["dnt", "sec-gpc"])
        if (request.headers.get(name) === "1") forwarded[name] = "1";
    }
    if (
      !readerMeasurementEnabled() ||
      operation.operationId !== "opened_v1_user_articles__article_id__open_post"
    )
      return forwarded;
    const raw = request.headers.get(readerMeasurementHeader);
    if (!raw || raw.length > 1536) return forwarded;
    try {
      const value = sanitizeReaderMeasurement(JSON.parse(raw), readerAcquisitionCampaigns());
      if (value) forwarded[readerMeasurementHeader] = JSON.stringify(value);
    } catch {
      /* A malformed optional diagnostic must not interrupt the reader. */
    }
    return forwarded;
  },
  traceHeaders,
  unavailableDetail: "User service unavailable",
});

type RouteContext = { params?: Promise<GatewayParameters> };

/** Next selects the operation; dedicated handlers supply any body validation. */
export function bindReaderOperation(
  operation: ReaderGatewayOperation,
  validateBody?: GatewayBodyValidator,
) {
  if (operation.policy.handler === "placement-query" && !validateBody)
    throw new Error("Placement operations require their dedicated handler");
  const route = new GatewayRoute(operation);
  return async (request: Request, context: RouteContext): Promise<Response> => {
    if (request.method !== operation.method) return methodNotAllowed([operation.method]);
    const path = route.bind((await context.params) ?? {});
    if (path === null) return Response.json({ detail: "Not found" }, { status: 404 });
    return transport.forward(request, operation, path, { validateBody });
  };
}

export function rejectReaderMethod(allowedMethods: readonly string[]) {
  return () => methodNotAllowed(allowedMethods);
}

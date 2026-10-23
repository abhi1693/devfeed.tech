import "server-only";
import { after } from "next/server";
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
import { userApiOrigin, userWebOrigin, userRequestOriginAllowed, xPixelEnabled } from "./config";
import { ReaderCookies } from "./reader-cookies";

const cookies = new ReaderCookies(xPixelEnabled);
const transport = new GatewayTransport<ReaderGatewayOperation>({
  apiOrigin: userApiOrigin,
  webOrigin: userWebOrigin,
  originAllowed: userRequestOriginAllowed,
  filterCookies: (value, operation) => cookies.filter(value, operation.policy.cookiePolicy),
  traceHeaders,
  unavailableDetail: "User service unavailable",
  afterResponse: after,
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

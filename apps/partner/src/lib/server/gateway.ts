import "server-only";
import { after } from "next/server";
import { GatewayTransport, methodNotAllowed } from "@devfeed/http/forwarding";
import { GatewayRoute, type GatewayOperation, type GatewayParameters } from "@devfeed/http/gateway";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { partnerApiOrigin, partnerWebOrigin } from "./config";

const transport = new GatewayTransport({
  apiOrigin: partnerApiOrigin,
  webOrigin: partnerWebOrigin,
  traceHeaders,
  afterResponse: after,
  unavailableDetail: "Partner service unavailable",
});

type RouteContext = { params?: Promise<GatewayParameters> };

/** Next selects the concrete route and method; this binds its declared operation. */
export function bindPartnerOperation(operation: GatewayOperation) {
  const route = new GatewayRoute(operation);
  return async (request: Request, context: RouteContext): Promise<Response> => {
    if (request.method !== operation.method) return methodNotAllowed([operation.method]);
    const path = route.bind((await context.params) ?? {});
    if (path === null) return Response.json({ detail: "Not found" }, { status: 404 });
    return transport.forward(request, operation, path);
  };
}

export function rejectPartnerMethod(allowedMethods: readonly string[]) {
  return () => methodNotAllowed(allowedMethods);
}

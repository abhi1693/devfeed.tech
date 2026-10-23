import "server-only";
import { after } from "next/server";
import { GatewayTransport, methodNotAllowed } from "@devfeed/http/forwarding";
import { GatewayRouter } from "@devfeed/http/gateway";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { adminGatewayOperations } from "../api/generated/gateway-operations";
import { adminApiOrigin, adminWebOrigin } from "./config";

const router = new GatewayRouter({ operations: adminGatewayOperations });
const transport = new GatewayTransport({
  apiOrigin: adminApiOrigin,
  webOrigin: adminWebOrigin,
  traceHeaders,
  afterResponse: after,
  unavailableDetail: "Admin service unavailable",
});

export async function gateway(request: Request, segments: string[]) {
  const match = router.resolve(request.method, segments);
  if (match.kind === "method-not-allowed") return methodNotAllowed(match.allowedMethods);
  if (match.kind !== "matched") return Response.json({ detail: "Not found" }, { status: 404 });
  return transport.forward(request, match.operation, match.path);
}

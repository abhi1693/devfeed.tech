import { receiveTelemetry } from "@devfeed/telemetry/receiver";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  return receiveTelemetry(request, "web");
}

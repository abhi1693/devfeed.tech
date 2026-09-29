import { mcpPublicUrl } from "@/lib/server/config";

export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store" };

export function GET() {
  try {
    return Response.json({ url: mcpPublicUrl() }, { headers });
  } catch {
    return Response.json({ detail: "MCP configuration is unavailable" }, { status: 503, headers });
  }
}

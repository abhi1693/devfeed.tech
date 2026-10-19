import contract from "../../../../reader-openapi.json";
import { publicSiteOrigin } from "@/lib/server/config";

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json(
    { ...contract, servers: [{ url: publicSiteOrigin() }] },
    { headers: { "Cache-Control": "public, max-age=300" } },
  );
}

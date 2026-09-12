import { catalogRoute } from "@/lib/server/catalog-route";
export const dynamic = "force-dynamic";
export function GET(request: Request) { return catalogRoute(request, "topics"); }

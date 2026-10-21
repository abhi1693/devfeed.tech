import { getTag, UserApiError } from "@/lib/api";
import { collectionRoute } from "@/lib/reader-routes";
export const dynamic = "force-dynamic";
export async function GET(request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const headers = { "Cache-Control": "no-store" };
  const { slug } = await params;
  if (!collectionRoute("tags", slug))
    return Response.json({ detail: "Not found" }, { status: 404, headers });
  try {
    return Response.json(
      await getTag(slug, request.signal, new URL(request.url).searchParams.get("by_id") === "true"),
      { headers },
    );
  } catch (error) {
    return Response.json(
      { detail: "Couldn’t load tag" },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

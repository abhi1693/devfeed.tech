import { getTopic, UserApiError } from "@/lib/api";
import { cardImage } from "@/lib/server/card-image";

export const dynamic = "force-dynamic";

/** Export only the catalog's configured logo, never a caller-supplied image URL. */
export async function GET(request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
  const { slug } = await params;
  if (!/^[a-z0-9][a-z0-9-]{0,199}$/i.test(slug))
    return Response.json({ detail: "Not found" }, { status: 404, headers });
  try {
    const topic = await getTopic(slug, request.signal);
    const image = await cardImage(topic.logo_url, "logo");
    if (!image) return Response.json({ detail: "Logo unavailable" }, { status: 404, headers });
    return Response.json({ image }, { headers });
  } catch (error) {
    return Response.json(
      { detail: "Logo unavailable" },
      {
        status: error instanceof UserApiError ? error.status : 503,
        headers,
      },
    );
  }
}

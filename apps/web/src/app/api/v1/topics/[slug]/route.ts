import { getTopic, UserApiError } from "@/lib/api";
export const dynamic = "force-dynamic";
export async function GET(request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const headers = { "Cache-Control": "no-store" };
  const { slug } = await params;
  if (!/^[a-z0-9][a-z0-9-]{0,199}$/i.test(slug))
    return Response.json({ detail: "Not found" }, { status: 404, headers });
  try {
    return Response.json(await getTopic(slug, request.signal), { headers });
  } catch (error) {
    return Response.json(
      { detail: "Couldn’t load topic" },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

import { getArticle, getTopic, UserApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const headers = { "Cache-Control": "no-store" };
  const { slug } = await params;
  if (!/^[a-z0-9][a-z0-9-]{0,199}$/i.test(slug))
    return Response.json({ detail: "Article not found" }, { status: 404, headers });
  try {
    const article = await getArticle(slug, request.signal);
    const featured = article.topics.find((topic) => topic.role === "primary") ?? article.topics[0];
    const topic = featured ? await getTopic(featured.slug, request.signal).catch(() => null) : null;
    return Response.json({ article, topic }, { headers });
  } catch (error) {
    return Response.json(
      { detail: "Couldn’t load the article" },
      {
        status: error instanceof UserApiError ? error.status : 503,
        headers,
      },
    );
  }
}

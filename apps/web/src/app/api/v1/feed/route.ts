import { getFeed, UserApiError } from "@/lib/api";
import { parseFilters } from "@/lib/feed-query";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  try {
    const filters = parseFilters(Object.fromEntries(new URL(request.url).searchParams));
    return Response.json(await getFeed(filters, request.signal, request.headers.get("cookie") ?? ""), { headers });
  } catch (error) {
    return Response.json({ detail: "Couldn’t load the feed" }, {
      status: error instanceof UserApiError ? error.status : 503,
      headers,
    });
  }
}

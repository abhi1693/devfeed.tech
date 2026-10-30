import { getSources, getTopics, UserApiError } from "@/lib/api";
import { CATALOG_PAGE_SIZE, catalogFilters, catalogPage } from "@/lib/catalog-page";

export async function catalogRoute(request: Request, kind: "topics" | "sources") {
  const headers = { "Cache-Control": "no-store" };
  const params = new URL(request.url).searchParams;
  const { query, offset } = catalogFilters(params, kind);
  try {
    const items =
      kind === "topics"
        ? await getTopics(
            offset,
            CATALOG_PAGE_SIZE + 1,
            request.signal,
            params.get("sort") === "articles" ? "articles" : "name",
            query,
            request.headers.get("cookie") ?? "",
          )
        : await getSources(
            offset,
            CATALOG_PAGE_SIZE + 1,
            request.signal,
            query,
            request.headers.get("cookie") ?? "",
          );
    return Response.json(catalogPage(items, offset), { headers });
  } catch (error) {
    return Response.json(
      { detail: `Couldn’t load ${kind}` },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

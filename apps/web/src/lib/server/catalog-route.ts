import { getSources, getTopics, UserApiError } from "@/lib/api";
import { CATALOG_PAGE_SIZE, catalogOffset, catalogPage } from "@/lib/catalog-page";

export async function catalogRoute(request: Request, kind: "topics" | "sources") {
  const headers = { "Cache-Control": "no-store" };
  const params = new URL(request.url).searchParams;
  const offset = catalogOffset(params.get("offset"));
  try {
    const items =
      kind === "topics"
        ? await getTopics(
            offset,
            CATALOG_PAGE_SIZE,
            request.signal,
            params.get("sort") === "articles" ? "articles" : "name",
          )
        : await getSources(offset, CATALOG_PAGE_SIZE, request.signal);
    return Response.json(catalogPage(items, offset), { headers });
  } catch (error) {
    return Response.json(
      { detail: `Couldn’t load ${kind}` },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

import { getTags, UserApiError } from "@/lib/api";
import { CATALOG_PAGE_SIZE, catalogFilters, catalogPage } from "@/lib/catalog-page";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  const params = new URL(request.url).searchParams;
  const { offset, query, letter } = catalogFilters(params, "tags");
  try {
    return Response.json(
      catalogPage(await getTags(offset, CATALOG_PAGE_SIZE + 1, query, letter), offset),
      {
        headers,
      },
    );
  } catch (error) {
    return Response.json(
      { detail: "Couldn’t load tags" },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

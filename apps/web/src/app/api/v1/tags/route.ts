import { getTags, UserApiError } from "@/lib/api";
import { catalogOffset, catalogPage } from "@/lib/catalog-page";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  const params = new URL(request.url).searchParams;
  const offset = catalogOffset(params.get("offset"));
  const query = (params.get("q") ?? "").trim().slice(0, 200);
  const letter = /^[A-Z#]$/.test(params.get("letter") ?? "") ? params.get("letter")! : "";
  try {
    return Response.json(catalogPage(await getTags(offset, 60, query, letter), offset), {
      headers,
    });
  } catch (error) {
    return Response.json(
      { detail: "Couldn’t load tags" },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

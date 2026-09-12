import { getSearch, UserApiError } from "@/lib/api";
import { searchKinds } from "@/lib/search";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const q = params.get("q") ?? "";
  const section = params.get("section") ?? undefined;
  const page = params.get("page") ?? "1";
  const headers = { "Cache-Control": "no-store" };
  if (
    q.length > 200 ||
    (section && !searchKinds.some((kind) => kind === section)) ||
    !/^[1-9]\d?$/.test(page) ||
    Number(page) > 80
  )
    return Response.json({ detail: "Invalid search request" }, { status: 422, headers });
  try {
    return Response.json(await getSearch(q, section, page, request.signal), { headers });
  } catch (error) {
    return Response.json(
      { detail: "Search is temporarily unavailable. Please try again." },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

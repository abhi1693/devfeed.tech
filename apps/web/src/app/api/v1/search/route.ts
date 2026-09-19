import { getSearch, UserApiError } from "@/lib/api";
import { parseSearchOptions, searchKinds } from "@/lib/search";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import { publicApiOrigin } from "@/lib/server/config";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const q = params.get("q") ?? "";
  const section = params.get("section") ?? undefined;
  const page = params.get("page") ?? "1";
  const options = parseSearchOptions(params);
  const today = new Date().toISOString().slice(0, 10);
  const headers = { "Cache-Control": "no-store" };
  if (
    ["sort", "date_from", "date_to"].some(
      (key) =>
        params.has(key) && params.get(key) !== options[key as "sort" | "date_from" | "date_to"],
    ) ||
    (options.date_from && options.date_to && options.date_from > options.date_to) ||
    options.date_from > today ||
    options.date_to > today ||
    q.length > 200 ||
    (section && !searchKinds.some((kind) => kind === section)) ||
    !/^[1-9]\d?$/.test(page) ||
    Number(page) > 80
  )
    return Response.json({ detail: "Invalid search request" }, { status: 422, headers });
  try {
    return Response.json(await getSearch(q, section, page, request.signal, options), { headers });
  } catch (error) {
    return Response.json(
      { detail: "Search is temporarily unavailable. Please try again." },
      { status: error instanceof UserApiError ? error.status : 503, headers },
    );
  }
}

export async function POST(request: Request) {
  try {
    const body = await request.text();
    if (body.length > 2048)
      return Response.json({ detail: "Invalid search event" }, { status: 422 });
    const response = await fetch(new URL("/v1/search/analytics/click", publicApiOrigin()), {
      method: "POST",
      headers: { "Content-Type": "application/json", ...traceHeaders() },
      body,
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(1500)]),
    });
    return new Response(null, { status: response.status });
  } catch {
    return new Response(null, { status: 204 });
  }
}

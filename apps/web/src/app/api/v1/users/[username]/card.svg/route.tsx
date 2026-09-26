import { publicDevCard } from "@/lib/server/public-dev-card";
import { renderDevCardSvg } from "@/lib/server/dev-card-svg";

export const dynamic = "force-dynamic";
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ username: string }> },
) {
  const profile = await publicDevCard((await params).username);
  if (!profile)
    return new Response("Card unavailable", {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  return new Response(await renderDevCardSvg(profile), {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy":
        "default-src 'none'; img-src data: http: https:; style-src 'unsafe-inline'; sandbox",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

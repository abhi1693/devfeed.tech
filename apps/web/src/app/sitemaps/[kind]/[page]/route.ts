import { sitemapPart } from "@/lib/server/sitemaps";
export const dynamic = "force-dynamic";
export async function GET(
  request: Request,
  { params }: { params: Promise<{ kind: string; page: string }> },
) {
  const { kind, page } = await params;
  return sitemapPart(request, kind, page);
}

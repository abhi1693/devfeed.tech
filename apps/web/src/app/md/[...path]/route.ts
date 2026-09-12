import { publicMarkdown } from "@/lib/server/ai-content";
export const dynamic = "force-dynamic";
export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
  return publicMarkdown(request, (await params).path);
}

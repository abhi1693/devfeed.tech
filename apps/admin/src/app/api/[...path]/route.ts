import { gateway } from "@/lib/server/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return gateway(request, (await context.params).path);
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };

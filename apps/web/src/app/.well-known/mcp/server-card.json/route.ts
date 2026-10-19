import { mcpPublicUrl } from "@/lib/server/config";

export const dynamic = "force-dynamic";
const MAX_CARD_BYTES = 65_536;

export async function GET(request: Request) {
  try {
    const endpoint = mcpPublicUrl();
    if (!endpoint)
      return Response.json(
        { detail: "Public MCP endpoint is not configured" },
        { status: 404, headers: { "Cache-Control": "no-store" } },
      );
    const canonical = new URL(endpoint);
    if (
      canonical.search ||
      (canonical.protocol !== "https:" &&
        !(
          canonical.protocol === "http:" &&
          ["localhost", "127.0.0.1", "[::1]"].includes(canonical.hostname)
        ))
    )
      throw new Error("Invalid public MCP endpoint");
    canonical.pathname = canonical.pathname.replace(/\/$/, "") + "/server-card";
    const response = await fetch(canonical, {
      headers: { Accept: "application/mcp-server-card+json, application/json" },
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(8000)]),
    });
    if (!response.ok || !response.body) throw new Error("MCP card is unavailable");
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > MAX_CARD_BYTES) throw new Error("MCP card is too large");
        chunks.push(value);
      }
    } finally {
      await reader.cancel();
    }
    const card = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (
      typeof card?.endpoint !== "string" ||
      new URL(card.endpoint).href !== endpoint ||
      typeof card?.serverInfo?.name !== "string" ||
      !card.serverInfo.name ||
      typeof card?.serverInfo?.version !== "string" ||
      !card.serverInfo.version ||
      !card.capabilities ||
      typeof card.capabilities !== "object" ||
      Array.isArray(card.capabilities)
    )
      throw new Error("Invalid MCP card");
    return Response.json(card, {
      headers: {
        "Cache-Control": "public, max-age=300",
        "Access-Control-Allow-Origin": "*",
        "Content-Location": canonical.href,
        Link: `<${canonical.href}>; rel="canonical"`,
      },
    });
  } catch {
    return Response.json(
      { detail: "MCP server card is unavailable" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}

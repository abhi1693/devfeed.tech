import { extensionPayload } from "@/lib/extension-analytics";
import { extensionOriginAllowed } from "@/lib/server/config";

export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store" };
function configuration() {
  if (process.env.DEVFEED_EXTENSION_ANALYTICS_ENABLED !== "true") return null;
  const id = process.env.GOOGLE_ANALYTICS_ID?.trim() || "G-N4V5CW5C0M";
  const secret = process.env.DEVFEED_EXTENSION_GA_API_SECRET?.trim();
  if (!/^G-[A-Z0-9]+$/.test(id) || !secret) return null;
  return { id, secret };
}
function allowed(request: Request) {
  try {
    return extensionOriginAllowed(request.headers.get("origin"));
  } catch {
    return false;
  }
}
export function GET(request: Request) {
  return Response.json(
    { enabled: (!request.headers.has("origin") || allowed(request)) && !!configuration() },
    { headers },
  );
}

// Bound relay traffic and memory per server process; this is abuse mitigation,
// not authentication. Browser Origin alone cannot authenticate installed clients.
const clients = new Map<string, number>();
let minute = 0;
let total = 0;
function permit(client: string) {
  const now = Math.floor(Date.now() / 60000);
  if (now !== minute) {
    clients.clear();
    total = 0;
    minute = now;
  }
  const used = clients.get(client) ?? 0;
  if (total >= 1000 || used >= 120) return false;
  clients.set(client, used + 1);
  total++;
  return true;
}
async function boundedJson(request: Request) {
  if (!request.body || Number(request.headers.get("content-length")) > 4096)
    throw new Error("Invalid body");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) {
        await reader.cancel();
        throw new Error("Body too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return JSON.parse(new TextDecoder().decode(bytes));
}
export async function POST(request: Request) {
  if (!allowed(request)) return new Response(null, { status: 403, headers });
  const config = configuration();
  if (!config) return new Response(null, { status: 204, headers });
  if (request.headers.get("content-type")?.split(";")[0] !== "application/json")
    return new Response(null, { status: 415, headers });
  let payload;
  try {
    payload = extensionPayload(await boundedJson(request));
  } catch {
    return new Response(null, { status: 400, headers });
  }
  if (!payload) return new Response(null, { status: 400, headers });
  if (!permit(payload.client_id))
    return new Response(null, { status: 429, headers: { ...headers, "Retry-After": "60" } });
  const url = new URL("https://www.google-analytics.com/mp/collect");
  url.searchParams.set("measurement_id", config.id);
  url.searchParams.set("api_secret", config.secret);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return new Response(null, { status: response.ok ? 204 : 502, headers });
  } catch {
    // Do not log the upstream URL: it contains the Measurement Protocol secret.
    return new Response(null, { status: 502, headers });
  }
}

import { recordDelivery, type DeliveryOutcome } from "./delivery";
import { normalizeBody, type BrowserSettings } from "./privacy";

export function browserSettings(app: "web" | "admin"): BrowserSettings {
  return {
    app,
    enabled: process.env.DEVFEED_FARO_ENABLED === "true",
    version: process.env.DEVFEED_BUILD_VERSION || process.env.DEVFEED_VERSION || "development",
    environment: process.env.DEVFEED_TELEMETRY_ENVIRONMENT || "development",
  };
}
let tokens = 40;
let updated = Date.now();
function accept() {
  const now = Date.now();
  tokens = Math.min(40, tokens + ((now - updated) / 1000) * 20);
  updated = now;
  if (tokens < 1) return false;
  tokens -= 1;
  return true;
}
export async function receiveTelemetry(request: Request, app: "web" | "admin"): Promise<Response> {
  const reply = (status: number, outcome: DeliveryOutcome) => {
    recordDelivery(status, outcome);
    return new Response(null, { status, headers: { "Cache-Control": "no-store" } });
  };
  const settings = browserSettings(app);
  if (!settings.enabled) return reply(404, "disabled");
  if (!process.env.DEVFEED_FARO_COLLECTOR_URL || !process.env.DEVFEED_FARO_API_KEY)
    return reply(404, "collector_unconfigured");
  const origin = process.env[app === "web" ? "DEVFEED_USER_BASE_URL" : "DEVFEED_ADMIN_BASE_URL"];
  if (!origin) return reply(403, "origin_unconfigured");
  if (!request.headers.get("origin")) return reply(403, "origin_missing");
  if (request.headers.get("origin") !== origin) return reply(403, "origin_mismatch");
  if (request.headers.get("content-encoding")) return reply(403, "content_encoding");
  if (!request.headers.get("content-type")?.startsWith("application/json"))
    return reply(415, "unsupported_type");
  if (Number(request.headers.get("content-length")) > 65_536) return reply(413, "too_large");
  if (!accept()) return reply(429, "rate_limited");
  const reader = request.body?.getReader();
  if (!reader) return reply(400, "missing_body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  let forwarding = false;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    void reader.cancel();
  }, 2000);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 65_536) {
        await reader.cancel();
        return reply(413, "too_large");
      }
      chunks.push(value);
    }
    if (timedOut) return reply(408, "body_timeout");
    clearTimeout(timer);
    const raw = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      raw.set(chunk, offset);
      offset += chunk.length;
    }
    const body = normalizeBody(JSON.parse(new TextDecoder().decode(raw)), settings);
    forwarding = true;
    const response = await fetch(process.env.DEVFEED_FARO_COLLECTOR_URL, {
      method: "POST",
      redirect: "error",
      signal: AbortSignal.timeout(2000),
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": process.env.DEVFEED_FARO_API_KEY,
        Origin: origin,
      },
      body: JSON.stringify(body),
    });
    await response.body?.cancel();
    return response.ok ? reply(202, "accepted") : reply(503, "upstream_rejected");
  } catch {
    return forwarding ? reply(503, "upstream_error") : reply(400, "invalid_body");
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }
}

import { recordDelivery } from "./delivery";
import { sanitizeBody, type BrowserSettings } from "./privacy";

export function browserSettings(app: "web" | "admin"): BrowserSettings {
  return {
    app,
    enabled: process.env.DEVFEED_FARO_ENABLED === "true",
    version: process.env.DEVFEED_VERSION || "development",
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
  const reply = (status: number) => {
    recordDelivery(status);
    return new Response(null, { status, headers: { "Cache-Control": "no-store" } });
  };
  const settings = browserSettings(app);
  if (
    !settings.enabled ||
    !process.env.DEVFEED_FARO_COLLECTOR_URL ||
    !process.env.DEVFEED_FARO_API_KEY
  )
    return reply(404);
  const origin = process.env[app === "web" ? "DEVFEED_USER_BASE_URL" : "DEVFEED_ADMIN_BASE_URL"];
  if (
    !origin ||
    request.headers.get("origin") !== origin ||
    request.headers.get("content-encoding")
  )
    return reply(403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return reply(415);
  if (Number(request.headers.get("content-length")) > 65_536) return reply(413);
  if (!accept()) return reply(429);
  const reader = request.body?.getReader();
  if (!reader) return reply(400);
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
        return reply(413);
      }
      chunks.push(value);
    }
    if (timedOut) return reply(408);
    clearTimeout(timer);
    const raw = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      raw.set(chunk, offset);
      offset += chunk.length;
    }
    const body = sanitizeBody(JSON.parse(new TextDecoder().decode(raw)), settings);
    // Only the configured application origin can identify private source maps.
    for (const exception of body.exceptions) {
      const stack = exception?.stacktrace as { frames: { filename: string }[] };
      for (const frame of stack.frames) frame.filename = origin + frame.filename;
    }
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
    return reply(response.ok ? 202 : 503);
  } catch {
    return reply(forwarding ? 503 : 400);
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }
}

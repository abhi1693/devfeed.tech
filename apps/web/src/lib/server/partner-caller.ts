import "server-only";
import { createHmac } from "node:crypto";
import { isIP } from "node:net";

export function partnerCallerHeaders(
  request: Request,
  upstreamPath: string,
): Record<string, string> {
  const ipHeader = process.env.DEVFEED_PARTNER_CALLER_IP_HEADER?.trim();
  if (!ipHeader) return {};

  const key = process.env.DEVFEED_PARTNER_CALLER_KEY;
  if (!key?.trim() || Buffer.byteLength(key, "utf8") < 32)
    throw new Error("DEVFEED_PARTNER_CALLER_KEY must contain at least 32 bytes");
  const ip = request.headers.get(ipHeader);
  if (!ip || !isIP(ip) || ip.includes("%"))
    throw new Error("Configured partner caller header must contain one IP address");

  const payload = `v1.${Math.floor(Date.now() / 1000)}.${ip}`;
  const signingKey = createHmac("sha256", key).update("devfeed:partner-caller:v1").digest();
  const signature = createHmac("sha256", signingKey)
    .update(`${payload}\nPOST\n${upstreamPath}`)
    .digest("hex");
  return { "X-DevFeed-Partner-Caller": `${payload}.${signature}` };
}

import { randomBytes } from "node:crypto";
import {
  preferredSourceWidgetHtml,
  preferredSourceWidgetPolicy,
} from "@/lib/server/preferred-source-widget";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  const nonce =
    request.headers.get("content-security-policy")?.match(/'nonce-([A-Za-z0-9+/=_-]+)'/)?.[1] ??
    randomBytes(16).toString("base64");
  const theme = new URL(request.url).searchParams.get("theme") === "dark" ? "dark" : "light";
  return new Response(preferredSourceWidgetHtml(nonce, theme), {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": preferredSourceWidgetPolicy(nonce),
      "Cache-Control": "private, no-store",
      "X-Robots-Tag": "noindex, nofollow",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
    },
  });
}

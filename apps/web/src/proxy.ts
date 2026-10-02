import { randomBytes } from "node:crypto";
import { createDualmarkMiddleware } from "@dualmark/nextjs";
import { toMarkdownPath } from "@dualmark/core";
import { NextRequest, NextResponse } from "next/server";
import { aiRoute } from "@/lib/ai-routes";
import { publicSiteOrigin } from "@/lib/server/config";

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === "/metrics" || pathname.startsWith("/metrics/")) {
    return new NextResponse(null, { status: 404 });
  }
  const nonce = randomBytes(16).toString("base64");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https://www.googletagmanager.com https://*.clarity.ms${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}`,
    "script-src-attr 'none'",
    "style-src 'self' 'unsafe-inline'",
    `style-src-elem 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' https: data: blob:",
    "font-src 'self' data:",
    "connect-src 'self' https://*.google-analytics.com https://www.googletagmanager.com https://*.clarity.ms https://c.bing.com",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const headers = new Headers(request.headers);
  headers.set("Content-Security-Policy", csp);
  const next = () => {
    const response = NextResponse.next({ request: { headers } });
    response.headers.set("Content-Security-Policy", csp);
    return response;
  };

  const explicit = pathname.endsWith(".md");
  const page = explicit ? pathname.slice(0, -3) : pathname;
  // Never negotiate actions, React's navigation protocol, assets, or private routes.
  if (
    !["GET", "HEAD"].includes(request.method) ||
    request.headers.has("rsc") ||
    request.headers.has("next-action") ||
    request.headers.has("next-router-prefetch") ||
    !aiRoute(page) ||
    (!explicit && ["/tags", "/index"].includes(page))
  )
    return next();
  const response = await createDualmarkMiddleware({ siteUrl: publicSiteOrigin() })(request);
  const vary = new Set(
    (response.headers.get("Vary") ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  );
  vary.add("Accept");
  vary.add("User-Agent");
  if (response.headers.has("x-middleware-next")) {
    const pageResponse = next();
    pageResponse.headers.set("Vary", [...vary].join(", "));
    // Next owns the final HTML Vary header. Keep this representation uncacheable.
    pageResponse.headers.set("Cache-Control", "private, no-store");
    pageResponse.headers.set(
      "Link",
      `<${publicSiteOrigin()}${toMarkdownPath(page)}${search}>; rel="alternate"; type="text/markdown"`,
    );
    return pageResponse;
  }
  response.headers.set("Vary", [...vary].join(", "));
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: ["/((?!_next/|api/|md/|favicon.ico|llms.txt|llms-full.txt|robots.txt|sitemap).*)"],
};

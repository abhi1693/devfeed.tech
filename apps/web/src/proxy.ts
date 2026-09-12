import { createDualmarkMiddleware } from "@dualmark/nextjs";
import { toMarkdownPath } from "@dualmark/core";
import { NextRequest, NextResponse } from "next/server";
import { aiRoute } from "@/lib/ai-routes";
import { publicSiteOrigin } from "@/lib/server/config";

export async function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
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
    return NextResponse.next();
  const response = await createDualmarkMiddleware({ siteUrl: publicSiteOrigin() })(request);
  const vary = new Set(
    (response.headers.get("Vary") ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  );
  vary.add("Accept");
  vary.add("User-Agent");
  response.headers.set("Vary", [...vary].join(", "));
  if (response.headers.has("x-middleware-next")) {
    // Next owns the final HTML Vary header. Keep this representation uncacheable,
    // including any public page that becomes statically rendered in the future.
    response.headers.set("Cache-Control", "private, no-store");
    response.headers.set(
      "Link",
      `<${publicSiteOrigin()}${toMarkdownPath(page)}${search}>; rel="alternate"; type="text/markdown"`,
    );
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/|api/|md/|favicon.ico|llms.txt|llms-full.txt|robots.txt|sitemap).*)"],
};

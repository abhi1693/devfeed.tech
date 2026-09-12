import type { NextConfig } from "next";
import path from "node:path";
import { withDualmark } from "@dualmark/nextjs";

const config: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  poweredByHeader: false,
  // Canonicals must be in the initial head for every crawler and reader.
  htmlLimitedBots: /.*/,
  // Inlined into the artifact, shared by replicas; used only for empty public pages.
  env: { DEVFEED_WEB_BUILD_TIME: new Date().toISOString() },
  async redirects() {
    return [
      {
        source: "/sitemaps/:kind/:page",
        destination: "/sitemap-:kind-:page.xml",
        permanent: true,
      },
    ];
  },
  async rewrites() {
    return {
      beforeFiles: [
        {
          source: "/sitemap-:kind-:page.xml",
          destination: "/sitemaps/:kind/:page",
        },
      ],
    };
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
          },
        ],
      },
      ...["/login", "/preferences", "/my-feed", "/api/:path*"].map((source) => ({
        source,
        headers: [
          { key: "Cache-Control", value: "private, no-store" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      })),
    ];
  },
};
export default withDualmark(config, {
  siteUrl: process.env.DEVFEED_USER_BASE_URL || "https://devfeed.tech",
});

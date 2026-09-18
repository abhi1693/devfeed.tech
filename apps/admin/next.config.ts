import { version } from "./package.json";
import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
  env: { DEVFEED_BUILD_VERSION: version },
  output: "standalone",
  productionBrowserSourceMaps: true,
  serverExternalPackages: ["@pyroscope/nodejs", "@prometheus-io/client", "@opentelemetry/sdk-node"],
  outputFileTracingRoot: path.join(import.meta.dirname, "../.."),
  poweredByHeader: false,
  // Next dev request logs otherwise include the authorization code in callback URLs.
  logging: { incomingRequests: false },
  // Explicit LAN dev origins can be supplied without altering production trust.
  allowedDevOrigins: process.env.DEVFEED_ADMIN_DEV_ORIGINS?.split(",").filter(Boolean),
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
          },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};
export default config;

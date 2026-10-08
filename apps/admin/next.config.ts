import { version } from "./package.json";
import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
  env: { DEVFEED_BUILD_VERSION: version },
  output: "standalone",
  // Nonce-based CSP requires request-time rendering; keep Cache Components disabled.
  reactCompiler: true,
  experimental: {
    agentUpgrade: "latest",
    turbopackRustReactCompiler: true,
    turbopackGc: true,
    turbopackLazyDynamicImports: true,
    turbopackPluginRuntimeStrategy: "workerThreads",
  },
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
          { key: "Strict-Transport-Security", value: "max-age=31536000" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=()",
          },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};
export default config;

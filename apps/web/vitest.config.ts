import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
      "server-only": path.resolve(import.meta.dirname, "tests/server-only.ts"),
    },
  },
  oxc: { jsx: { runtime: "automatic" } },
  test: {
    server: { deps: { inline: ["@dualmark/nextjs"] } },
    environment: "node",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/setup.ts"],
    restoreMocks: true,
    coverage: {
      provider: "v8",
      allowExternal: true,
      reporter: ["text-summary", "lcovonly"],
      reportsDirectory: "../../reports/coverage/web",
      include: [
        "src/**/*.{ts,tsx}",
        "public/web-push-sw.js",
        `${path.resolve(import.meta.dirname, "../../packages")}/*/src/**/*.{ts,tsx}`,
        `${path.resolve(import.meta.dirname, "../../packages/theme")}/*.ts`,
        path.resolve(import.meta.dirname, "../../scripts/assets/browser-icons.mjs"),
        path.resolve(import.meta.dirname, "../../scripts/assets/profile-icons.mjs"),
        path.resolve(import.meta.dirname, "../../scripts/ci/accessibility-report.mjs"),
        path.resolve(import.meta.dirname, "../extensions/build.mjs"),
      ],
      exclude: ["**/*.d.ts"],
    },
  },
});

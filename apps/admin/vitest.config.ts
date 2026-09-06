import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  resolve: { alias: {
    "@": path.resolve(import.meta.dirname, "src"),
    "server-only": path.resolve(import.meta.dirname, "tests/server-only.ts"),
  } },
  esbuild: { jsx: "automatic" },
  test: { environment: "node", include: ["tests/**/*.test.{ts,tsx}"], setupFiles: ["./tests/setup.ts"], restoreMocks: true },
});

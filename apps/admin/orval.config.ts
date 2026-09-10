import { defineConfig } from "orval";

export default defineConfig({
  admin: {
    input: { target: "./openapi.json", filters: { tags: ["admin-auth", "admin-settings", "admin-ai-connection", "admin-overview", "admin-automation", "admin-knowledge", "admin-sources", "admin-articles", "admin-topics", "admin-taxonomy", "admin-jobs", "admin-notifications", "admin-workers"] } },
    output: {
      target: "./src/lib/api/generated/admin.ts",
      schemas: "./src/lib/api/generated/models",
      client: "fetch",
      mode: "split",
      clean: true,
      override: {
        mutator: { path: "./src/lib/api/client.ts", name: "adminFetch" },
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
});

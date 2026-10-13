import { unlink } from "node:fs/promises";
import { defineConfig } from "orval";

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const generatedDirectory = "./src/lib/api/generated";
const responsePaths = new Set([
  "/v1/partner/accounts",
  "/v1/partner/dashboard",
  "/v1/partner/inventory",
  "/v1/partner/connections",
  "/v1/partner/connections/{provider}",
  "/v1/partner/connections/{provider}/jobs",
  "/v1/partner/accounts/{account_id}/preferences",
  "/v1/partner/accounts/{account_id}/destination-preview",
  "/v1/partner/accounts/{account_id}/creative-preview",
  "/v1/partner/accounts/{account_id}/audience-topics",
  "/v1/partner/accounts/{account_id}/inventory",
  "/v1/partner/accounts/{account_id}/dashboard",
  "/v1/partner/accounts/{account_id}/connections",
  "/v1/partner/accounts/{account_id}/connections/{provider}",
  "/v1/partner/accounts/{account_id}/connections/{provider}/actions",
  "/v1/partner/accounts/{account_id}/connections/{provider}/jobs",
]);

export default defineConfig({
  models: {
    input: {
      target: "./openapi.json",
      override: {
        // Generate component types without an unused HTTP client.
        transformer: (document) => ({ ...document, paths: {} }),
      },
    },
    output: {
      // Keep targets explicit: Orval resolves an empty target to the app root.
      target: generatedDirectory + "/models-only.ts",
      schemas: generatedDirectory + "/models",
      client: "fetch",
      mode: "split",
      clean: false,
    },
    hooks: {
      afterAllFilesWrite: async () => {
        // Orval requires an explicit target, but zero operations need no client.
        await unlink(new URL("./src/lib/api/generated/models-only.ts", import.meta.url));
      },
    },
  },
  responses: {
    input: {
      target: "./openapi.json",
      override: {
        transformer: (document) => {
          const projected = structuredClone(document);
          projected.paths = Object.fromEntries(
            Object.entries(projected.paths ?? {}).filter(([path]) => responsePaths.has(path)),
          );
          // Backend responses serialize defaults. Require those fields instead
          // of allowing generated defaults to conceal a partial save response.
          for (const name of ["AccountOut", "AudienceInput"]) {
            const schema = projected.components?.schemas?.[name];
            if (!isObject(schema) || "$ref" in schema || !isObject(schema.properties)) {
              throw new Error("Missing response schema: " + name);
            }
            schema.required = Object.keys(schema.properties);
            for (const property of Object.values(schema.properties)) {
              if (isObject(property) && !("$ref" in property)) delete property.default;
            }
          }
          const formats = projected["x-devfeed-automatic-ad-formats"];
          const account = projected.components?.schemas?.AccountOut;
          if (
            !Array.isArray(formats) ||
            !formats.every((format) => typeof format === "string") ||
            !isObject(account) ||
            "$ref" in account ||
            !isObject(account.properties)
          ) {
            throw new Error("Missing automatic advertising format contract");
          }
          const adFormats = account.properties?.ad_formats;
          if (!isObject(adFormats) || "$ref" in adFormats || !isObject(adFormats.items)) {
            throw new Error("Missing account advertising format contract");
          }
          adFormats.items = { type: "string", enum: formats };
          return projected;
        },
      },
    },
    output: {
      target: generatedDirectory + "/validators.ts",
      schemas: false,
      client: "zod",
      mode: "single",
      clean: false,
      override: {
        zod: {
          version: 4,
          generate: { param: false, query: false, header: false, body: false, response: true },
        },
      },
    },
  },
});

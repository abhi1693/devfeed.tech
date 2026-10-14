import { describe, expect, it } from "vitest";
import { GatewayRouter, type GatewayOperation } from "@devfeed/http/gateway";
import { adminGatewayOperations } from "@/lib/api/generated/gateway-operations";

const identifier = "11111111-1111-4111-8111-111111111111";
const productId = "22222222-2222-4222-8222-222222222222";
const ordinaryPolicy = { bodyLimit: 1_000_000, bodyTimeoutMs: 2000, upstreamTimeoutMs: 45_000 };

function operation(
  operationId: string,
  path: string,
  method = "GET",
  parameters: GatewayOperation["parameters"] = [],
): GatewayOperation {
  return { operationId, method, path, parameters, policy: ordinaryPolicy };
}

function match(router: GatewayRouter, method: string, segments: readonly string[]) {
  const result = router.resolve(method, segments);
  expect(result.kind).toBe("matched");
  if (result.kind !== "matched")
    throw new Error(`Expected a matched operation, got ${result.kind}`);
  return result;
}

describe("ordered gateway operation matching", () => {
  it("selects a later full method match over an earlier path-only match", () => {
    const preview = operation("preview", "/v1/admin/sources/preview", "POST");
    const detail = operation("detail", "/v1/admin/sources/{id}", "GET", [
      { name: "id", converter: "str", schema: { type: "string", format: "uuid" } },
    ]);
    const router = new GatewayRouter({ operations: [preview, detail] });

    expect(match(router, "POST", ["v1", "admin", "sources", "preview"]).operation).toEqual(preview);
    expect(router.resolve("GET", ["v1", "admin", "sources", "preview"])).toEqual({
      kind: "invalid-parameters",
    });
    expect(match(router, "GET", ["v1", "admin", "sources", identifier]).operation).toEqual(detail);
  });

  it("keeps backend declaration order instead of sorting literals ahead of parameters", () => {
    const dynamic = operation("dynamic", "/v1/admin/resources/{name}", "GET", [
      { name: "name", converter: "str", schema: { type: "string" } },
    ]);
    const literal = operation("literal", "/v1/admin/resources/featured");
    const router = new GatewayRouter({ operations: [dynamic, literal] });

    const result = match(router, "GET", ["v1", "admin", "resources", "featured"]);
    expect(result.operation).toEqual(dynamic);
    expect(result.parameters).toEqual({ name: "featured" });
  });

  it("does not fall through after a syntactic match fails its semantic constraints", () => {
    const constrained = operation("constrained", "/v1/admin/resources/{name}", "GET", [
      { name: "name", converter: "str", schema: { type: "string", enum: ["approved"] } },
    ]);
    const fallback = operation("fallback", "/v1/admin/resources/{name}", "GET", [
      { name: "name", converter: "str", schema: { type: "string" } },
    ]);
    const router = new GatewayRouter({ operations: [constrained, fallback] });

    expect(router.resolve("GET", ["v1", "admin", "resources", "pending"])).toEqual({
      kind: "invalid-parameters",
    });
  });

  it("distinguishes a UUID route converter from a UUID constraint on a string converter", () => {
    const typed = operation("typed", "/v1/admin/resources/{id}", "GET", [
      { name: "id", converter: "uuid", schema: { type: "string", format: "uuid" } },
    ]);
    const named = operation("named", "/v1/admin/resources/{name}", "GET", [
      { name: "name", converter: "str", schema: { type: "string" } },
    ]);
    const router = new GatewayRouter({ operations: [typed, named] });

    expect(match(router, "GET", ["v1", "admin", "resources", "featured"]).operation).toEqual(named);
    expect(match(router, "GET", ["v1", "admin", "resources", identifier]).operation).toEqual(typed);
  });

  it("distinguishes unknown paths from unsupported methods without synthesizing HEAD or OPTIONS", () => {
    const router = new GatewayRouter({ operations: [operation("overview", "/v1/admin/overview")] });

    expect(router.resolve("GET", ["v1", "admin", "missing"])).toEqual({ kind: "not-found" });
    for (const method of ["POST", "HEAD", "OPTIONS"]) {
      expect(router.resolve(method, ["v1", "admin", "overview"])).toEqual({
        kind: "method-not-allowed",
        allowedMethods: ["GET"],
      });
    }
  });

  it("reports every declared method matching the path syntax when no method matches fully", () => {
    const router = new GatewayRouter({
      operations: [
        operation("preview", "/v1/admin/sources/preview", "POST"),
        operation("detail", "/v1/admin/sources/{id}", "GET", [
          { name: "id", converter: "str", schema: { type: "string", format: "uuid" } },
        ]),
      ],
    });

    const result = router.resolve("OPTIONS", ["v1", "admin", "sources", "preview"]);
    expect(result.kind).toBe("method-not-allowed");
    if (result.kind !== "method-not-allowed") throw new Error("Expected declared methods");
    expect(new Set(result.allowedMethods)).toEqual(new Set(["POST", "GET"]));
  });

  it.each([
    "abcdefab-cdef-4abc-8def-abcdefabcdef",
    "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF",
    "abcdefabcdef4abc8defabcdefabcdef",
    "00000000-0000-0000-0000-000000000000",
  ])("accepts UUID syntax without imposing a version or case restriction: %s", (value) => {
    const router = new GatewayRouter({
      operations: [
        operation("detail", "/v1/admin/resources/{id}", "GET", [
          { name: "id", converter: "str", schema: { type: "string", format: "uuid" } },
        ]),
      ],
    });

    expect(match(router, "GET", ["v1", "admin", "resources", value]).parameters).toEqual({
      id: value,
    });
  });

  it("applies string length constraints to Unicode code points", () => {
    const router = new GatewayRouter({
      operations: [
        operation("detail", "/v1/admin/resources/{name}", "GET", [
          {
            name: "name",
            converter: "str",
            schema: { type: "string", minLength: 2, maxLength: 2 },
          },
        ]),
      ],
    });

    expect(match(router, "GET", ["v1", "admin", "resources", "🚀💡"]).path).toBe(
      "/v1/admin/resources/%F0%9F%9A%80%F0%9F%92%A1",
    );
    expect(router.resolve("GET", ["v1", "admin", "resources", "🚀"])).toEqual({
      kind: "invalid-parameters",
    });
    expect(router.resolve("GET", ["v1", "admin", "resources", "🚀💡✨"])).toEqual({
      kind: "invalid-parameters",
    });
  });

  it("matches Unicode patterns using the same code-point semantics as generation", () => {
    const router = new GatewayRouter({
      operations: [
        operation("detail", "/v1/admin/resources/{name}", "GET", [
          {
            name: "name",
            converter: "str",
            schema: { type: "string", minLength: 1, maxLength: 1, pattern: "^.$" },
          },
        ]),
      ],
    });

    expect(match(router, "GET", ["v1", "admin", "resources", "🚀"]).parameters).toEqual({
      name: "🚀",
    });
  });

  it("builds an upstream path from decoded parameters without changing their logical value", () => {
    const router = new GatewayRouter({
      operations: [
        operation("worker", "/v1/admin/workers/{name}", "GET", [
          {
            name: "name",
            converter: "str",
            schema: { type: "string", minLength: 1, maxLength: 256, pattern: "^[a-zA-Z0-9_.:-]+$" },
          },
        ]),
      ],
    });

    const result = match(router, "GET", ["v1", "admin", "workers", "ai:host.1"]);
    expect(result.path).toBe("/v1/admin/workers/ai%3Ahost.1");
    expect(result.parameters).toEqual({ name: "ai:host.1" });
    expect(router.resolve("GET", ["v1", "admin", "workers", "a".repeat(257)])).toEqual({
      kind: "invalid-parameters",
    });
    expect(router.resolve("GET", ["v1", "admin", "workers", "bad!name"])).toEqual({
      kind: "invalid-parameters",
    });
  });

  it.each([
    "",
    ".",
    "..",
    "a/b",
    "a\\b",
    "a?query",
    "a#fragment",
    "%2e%2e",
    "%2F",
    "%252f",
    "a\0b",
    "a\nb",
    "a\u007fb",
    "\ud800",
    "\udfff",
  ])("rejects unsafe decoded or residual-encoded segments before route matching: %j", (value) => {
    const router = new GatewayRouter({
      operations: [
        operation("resource", "/v1/admin/resources/{name}", "GET", [
          { name: "name", converter: "str", schema: { type: "string" } },
        ]),
      ],
    });

    expect(router.resolve("GET", ["v1", "admin", "resources", value])).toEqual({
      kind: "not-found",
    });
  });

  it("captures a terminal catch-all while safely encoding each component", () => {
    const router = new GatewayRouter({
      operations: [
        operation("inbox", "/v1/admin/inbox/{path}", "POST", [
          { name: "path", converter: "path", schema: { type: "string" } },
        ]),
      ],
    });

    const result = match(router, "POST", [
      "v1",
      "admin",
      "inbox",
      "notifications",
      "item:1",
      "read",
    ]);
    expect(result.parameters).toEqual({ path: "notifications/item:1/read" });
    expect(result.path).toBe("/v1/admin/inbox/notifications/item%3A1/read");
    expect(router.resolve("POST", ["v1", "admin", "inbox", "notifications", "..", "read"])).toEqual(
      {
        kind: "not-found",
      },
    );
    expect(
      router.resolve("POST", ["v1", "admin", "inbox", "notifications", "a%2Fb", "read"]),
    ).toEqual({
      kind: "not-found",
    });
  });
});

describe("generated admin gateway contract", () => {
  const router = new GatewayRouter({ operations: adminGatewayOperations });
  const product = ["v1", "admin", "partner-accounts", identifier, "products"];

  it.each([
    ["POST", ["v1", "admin", "topic-discovery", "github"], 1_000_000, 2000, 120_000],
    ["POST", ["v1", "admin", "source-imports"], 6_100_000, 10_000, 150_000],
    ["POST", ["v1", "admin", "source-imports", identifier, "review"], 1_000_000, 2000, 150_000],
    ["POST", ["v1", "admin", "sources"], 1_000_000, 2000, 215_000],
    ["POST", ["v1", "admin", "sources", "preview"], 1_000_000, 2000, 305_000],
    ["POST", product, 65536, 2000, 45_000],
    ["PATCH", [...product, productId], 65536, 2000, 45_000],
    ["POST", [...product, productId, "ad-image"], 5 * 1024 * 1024 + 65536, 10_000, 45_000],
  ])(
    "preserves the declared body and upstream budgets for %s %j",
    (method, path, bodyLimit, bodyTimeoutMs, upstreamTimeoutMs) => {
      const result = match(router, method, path);
      expect(result.operation.policy).toEqual({ bodyLimit, bodyTimeoutMs, upstreamTimeoutMs });
    },
  );

  it("retains operations outside the generated browser client's tag filter", () => {
    expect(
      match(router, "GET", ["v1", "admin", "search-suggestions"]).operation.operationId,
    ).toBeTruthy();
    expect(
      match(router, "GET", ["v1", "admin", "ingestion", "jobs", identifier]).operation.operationId,
    ).toBeTruthy();
  });

  it.each([
    ["GET", ["stream"]],
    ["GET", ["preferences"]],
    ["PUT", ["preferences"]],
    ["POST", ["notifications", `notif_${"a".repeat(26)}`, "read"]],
  ])("retains the backend's schema-hidden inbox operation for %s %j", (method, suffix) => {
    const result = match(router, method, [
      "v1",
      "admin",
      "notifications",
      "chimely",
      "v1",
      "inbox",
      ...suffix,
    ]);
    expect(result.parameters).toEqual({ path: suffix.join("/") });
    expect(result.operation.policy.upstreamTimeoutMs).toBe(45_000);
  });

  it("validates real UUID and enum parameters rather than forwarding plausible-looking unknown operations", () => {
    expect(router.resolve("GET", [...product, "not-a-uuid"])).toEqual({
      kind: "not-found",
    });
    expect(router.resolve("GET", ["v1", "admin", "jobs", "not-a-job-kind"])).toEqual({
      kind: "invalid-parameters",
    });
    expect(
      router.resolve("POST", ["v1", "admin", "source-imports", "not-a-uuid", "review"]),
    ).toEqual({
      kind: "invalid-parameters",
    });
    expect(router.resolve("POST", [...product, productId, "ad-image", "extra"])).toEqual({
      kind: "not-found",
    });
    expect(router.resolve("POST", ["v1", "admin", "topic-discovery", "github-extra"])).toEqual({
      kind: "not-found",
    });
  });

  it("preserves the literal AI job list and applies the default policy only to declared ordinary operations", () => {
    expect(match(router, "GET", ["v1", "admin", "jobs", "ai-analysis"]).operation.operationId).toBe(
      "admin_ai_analysis_jobs_list",
    );
    expect(match(router, "GET", ["v1", "admin", "source-imports"]).operation.policy).toEqual(
      ordinaryPolicy,
    );
    expect(
      match(router, "POST", ["v1", "admin", "sources", identifier, "fetch"]).operation.policy,
    ).toEqual(ordinaryPolicy);
  });
});

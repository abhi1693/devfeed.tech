import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  adminGatewayOperations,
  generateAdminGateway,
  renderAdminGateway,
} from "../scripts/generate_admin_gateway.mjs";

const ordinaryPolicy = { bodyLimit: 1_000_000, bodyTimeoutMs: 2000, upstreamTimeoutMs: 45_000 };
const uuid = { type: "string", format: "uuid" };

function route(overrides = {}) {
  return {
    operationId: "admin_worker_get",
    method: "GET",
    path: "/v1/admin/workers/{name}",
    parameters: [
      {
        name: "name",
        converter: "str",
        schema: {
          type: "string",
          minLength: 1,
          maxLength: 256,
          pattern: "^[a-zA-Z0-9_.:-]+$",
          title: "Name",
        },
      },
    ],
    policy: { ...ordinaryPolicy },
    ...overrides,
  };
}

function document(routes = [route()]) {
  const paths = {};
  for (const item of routes) {
    paths[item.path] ??= {};
    paths[item.path][item.method.toLowerCase()] = {
      operationId: item.operationId,
      "x-devfeed-gateway": structuredClone(item.policy),
      parameters: item.parameters.map(({ name, schema }) => ({
        name,
        in: "path",
        required: true,
        schema: structuredClone(schema),
      })),
    };
  }
  return {
    openapi: "3.1.0",
    info: { version: "1.2.3" },
    paths,
    "x-devfeed-gateway-routes": structuredClone(routes),
  };
}

test("registration priority survives sorted OpenAPI paths and generation is deterministic", async () => {
  const input = document([
    route(),
    route({
      operationId: "admin_worker_statistics",
      path: "/v1/admin/workers/statistics",
      parameters: [],
    }),
  ]);
  const before = structuredClone(input);
  const normalized = adminGatewayOperations(input);
  assert.deepEqual(
    normalized.map(({ operationId }) => operationId),
    ["admin_worker_get", "admin_worker_statistics"],
  );
  const rendered = await renderAdminGateway(input);
  input.paths = Object.fromEntries(Object.entries(input.paths).reverse());
  assert.equal(await renderAdminGateway(input), rendered);
  assert.deepEqual(before["x-devfeed-gateway-routes"], input["x-devfeed-gateway-routes"]);
  assert.match(rendered, /import type \{ GatewayOperation \} from "@devfeed\/http\/gateway"/);
  assert.match(rendered, /as const satisfies readonly GatewayOperation\[\]/);
  assert.match(rendered, /^ \* OpenAPI spec version: 1\.2\.3$/m);
});

test("missing or malformed spec versions cannot enter the generated comment", async () => {
  for (const version of [
    undefined,
    null,
    123,
    "",
    "01.2.3",
    "1.2",
    "1.2.3-beta",
    "1.2.3 ",
    "1.2.3\n",
    "1.2.3\n */\nthrow new Error('injected')\n/*",
  ]) {
    const input = document();
    input.info.version = version;
    await assert.rejects(renderAdminGateway(input), /info.version.*release version/);
  }
  const missing = document();
  delete missing.info;
  await assert.rejects(renderAdminGateway(missing), /OpenAPI info must be an object/);
});

test("backend converter and endpoint policy survive without UUID or upload path inference", () => {
  const input = document([
    route({
      operationId: "admin_product_image_upload",
      method: "POST",
      path: "/v1/admin/partner-accounts/{account_id}/products/{product_id}/ad-image",
      parameters: [
        { name: "product_id", converter: "uuid", schema: uuid },
        { name: "account_id", converter: "str", schema: uuid },
      ],
      policy: {
        bodyLimit: 5 * 1024 * 1024 + 65536,
        bodyTimeoutMs: 10_000,
        upstreamTimeoutMs: 45_000,
      },
    }),
  ]);
  const [operation] = adminGatewayOperations(input);
  assert.deepEqual(operation.parameters, [
    { name: "account_id", converter: "str", schema: uuid },
    { name: "product_id", converter: "uuid", schema: uuid },
  ]);
  assert.equal(operation.policy.bodyLimit, 5 * 1024 * 1024 + 65536);
  assert.equal(operation.policy.bodyTimeoutMs, 10_000);
  input["x-devfeed-gateway-routes"][0].path =
    "/v1/admin/renamed-products/{account_id}/artwork/{product_id}";
  input.paths = {
    [input["x-devfeed-gateway-routes"][0].path]:
      input.paths["/v1/admin/partner-accounts/{account_id}/products/{product_id}/ad-image"],
  };
  assert.deepEqual(adminGatewayOperations(input)[0].policy, operation.policy);
});

test("export path-converter records remain explicit and do not synthesize HEAD or OPTIONS", () => {
  const exports = ["GET", "POST", "PUT"].map((method) =>
    route({
      operationId: `admin_exports_${method.toLowerCase()}`,
      method,
      path: "/v1/admin/exports/{path}",
      parameters: [{ name: "path", converter: "path", schema: { type: "string" } }],
    }),
  );
  const input = document([route()]);
  input["x-devfeed-gateway-routes"].push(...exports);
  assert.deepEqual(
    adminGatewayOperations(input).map(({ method }) => method),
    ["GET", "GET", "POST", "PUT"],
  );
  assert.equal(adminGatewayOperations(input)[1].parameters[0].converter, "path");
});

test("explicit HEAD and OPTIONS declarations are retained when the backend exports them", () => {
  const input = document(
    ["GET", "HEAD", "OPTIONS"].map((method) =>
      route({ operationId: `admin_worker_${method.toLowerCase()}`, method }),
    ),
  );
  assert.deepEqual(
    adminGatewayOperations(input).map(({ method }) => method),
    ["GET", "HEAD", "OPTIONS"],
  );
});

test("local parameter schema references are resolved without losing bounds or enum values", () => {
  const input = document([
    route({
      operationId: "admin_panel_get",
      path: "/v1/admin/overview/{panel}",
      parameters: [
        {
          name: "panel",
          converter: "str",
          schema: { $ref: "#/components/schemas/Panel", description: "Requested panel" },
        },
      ],
    }),
  ]);
  input.components = {
    schemas: { Panel: { type: "string", enum: ["readers", "click-depth"], maxLength: 30 } },
  };
  assert.deepEqual(adminGatewayOperations(input)[0].parameters[0].schema, {
    type: "string",
    enum: ["readers", "click-depth"],
    maxLength: 30,
  });
});

test("unsupported or unsafe route contracts fail before producing broader routing", () => {
  const invalid = [
    route({ method: "TRACE" }),
    route({ operationId: "" }),
    route({ path: "/v1/user/workers/{name}" }),
    route({ path: "//v1/admin/workers/{name}" }),
    route({ path: "/v1/admin/workers/../{name}" }),
    route({ path: "/v1/admin/workers/{name}?foo=bar" }),
    route({ path: "/v1/admin/workers/%2e%2e/{name}" }),
    route({ path: "/v1/admin/workers/{name:str}" }),
    route({ path: "/v1/admin/workers/name-{name}" }),
    route({ path: "/v1/admin/workers/{name}/{name}" }),
    route({ parameters: [] }),
    route({ parameters: [{ name: "other", converter: "str", schema: { type: "string" } }] }),
    route({ parameters: [{ name: "name", converter: "int", schema: { type: "string" } }] }),
    route({ parameters: [{ name: "name", converter: "uuid", schema: { type: "string" } }] }),
    route({
      path: "/v1/admin/workers/{name}/details",
      parameters: [{ name: "name", converter: "path", schema: { type: "string" } }],
    }),
  ];
  for (const item of invalid) {
    assert.throws(() => adminGatewayOperations(document([item])), undefined, JSON.stringify(item));
  }
});

test("unknown schema constraints, nullable values and incompatible regex syntax never disappear", () => {
  const invalid = [
    { type: "integer" },
    { type: ["string", "null"] },
    { type: "string", nullable: true },
    { type: "string", format: "url" },
    { type: "string", enum: [] },
    { type: "string", enum: ["a", "a"] },
    { type: "string", enum: [1] },
    { type: "string", minLength: -1 },
    { type: "string", maxLength: 1.5 },
    { type: "string", minLength: 3, maxLength: 2 },
    { type: "string", pattern: "(?i)worker" },
    { type: "string", pattern: 3 },
    { type: "string", const: "worker" },
    { $ref: "https://schema.example/Name" },
    { $ref: "#/components/schemas/Name", maxLength: 20 },
  ];
  for (const value of invalid) {
    const item = route({ parameters: [{ name: "name", converter: "str", schema: value }] });
    assert.throws(() => adminGatewayOperations(document([item])), undefined, JSON.stringify(value));
  }
  const cyclic = document([
    route({
      parameters: [
        { name: "name", converter: "str", schema: { $ref: "#/components/schemas/Name" } },
      ],
    }),
  ]);
  cyclic.components = { schemas: { Name: { $ref: "#/components/schemas/Name" } } };
  assert.throws(() => adminGatewayOperations(cyclic), /cyclic/);
});

test("missing, invalid and unknown transport policy fields fail rather than using defaults", () => {
  for (const value of [
    undefined,
    {},
    { ...ordinaryPolicy, bodyLimit: 0 },
    { ...ordinaryPolicy, bodyTimeoutMs: -1 },
    { ...ordinaryPolicy, upstreamTimeoutMs: NaN },
    { ...ordinaryPolicy, upstreamTimeoutMs: Number.MAX_SAFE_INTEGER + 1 },
    { ...ordinaryPolicy, readTimeoutMs: 2000 },
  ]) {
    assert.throws(() => adminGatewayOperations(document([route({ policy: value })])));
  }
});

test("documented operations cannot silently disappear or drift from ordered export", () => {
  for (const change of [
    (value) => delete value["x-devfeed-gateway-routes"],
    (value) => (value["x-devfeed-gateway-routes"] = []),
    (value) => delete value.paths["/v1/admin/workers/{name}"].get["x-devfeed-gateway"],
    (value) => (value.paths["/v1/admin/workers/{name}"].get.operationId = "other"),
    (value) => (value.paths["/v1/admin/workers/{name}"].get["x-devfeed-gateway"].bodyLimit = 100),
    (value) => (value.paths["/v1/admin/workers/{name}"].get.parameters[0].schema.maxLength = 257),
    (value) => (value.paths["/v1/admin/workers/{name}"].get.parameters = []),
    (value) => {
      value.paths["/v1/admin/new-operation"] = {
        post: { operationId: "new", "x-devfeed-gateway": ordinaryPolicy, parameters: [] },
      };
    },
  ]) {
    const value = document();
    change(value);
    assert.throws(() => adminGatewayOperations(value));
  }
});

test("duplicate routes, shadow-equivalent templates and reused operation IDs fail", () => {
  assert.throws(() => adminGatewayOperations(document([route(), route()])), /signature/);
  assert.throws(
    () =>
      adminGatewayOperations(
        document([
          route(),
          route({
            operationId: "other_worker",
            path: "/v1/admin/workers/{worker}",
            parameters: [{ ...route().parameters[0], name: "worker" }],
          }),
        ]),
      ),
    /signature/,
  );
  assert.throws(
    () =>
      adminGatewayOperations(
        document([route(), route({ path: "/v1/admin/other/{name}", method: "POST" })]),
      ),
    /operationId/,
  );
});

test("non-admin OpenAPI operations never enter the gateway manifest", () => {
  const value = document();
  value.paths["/health"] = { get: { operationId: "health" } };
  value.paths["/v1/user/auth/me"] = { get: { operationId: "user_me" } };
  assert.equal(adminGatewayOperations(value).length, 1);
});

test("generation check catches stale output without overwriting a reviewable file", async () => {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-admin-gateway-"));
  try {
    const source = join(directory, "openapi.json");
    const target = join(directory, "gateway-operations.ts");
    await writeFile(source, JSON.stringify(document()));
    await generateAdminGateway({ source, target });
    await generateAdminGateway({ source, target, check: true });
    await writeFile(target, "stale\n");
    await assert.rejects(generateAdminGateway({ source, target, check: true }), /stale/);
    assert.equal(await readFile(target, "utf8"), "stale\n");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("checked-in descriptors match the current authoritative backend export", async () => {
  await assert.doesNotReject(() => generateAdminGateway({ check: true }));
});

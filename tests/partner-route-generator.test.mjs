import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { transformSync } from "esbuild";
import {
  generatePartnerRoutes,
  partnerRoutePlan,
  renderPartnerRoutes,
} from "../scripts/generate_partner_routes.mjs";

const policy = { bodyLimit: 1_000_000, bodyTimeoutMs: 2000, upstreamTimeoutMs: 45_000 };
const uuid = { type: "string", format: "uuid" };

function route(overrides = {}) {
  return {
    operationId: "partnerAccountsList",
    method: "GET",
    path: "/v1/partner/accounts",
    parameters: [],
    policy: { ...policy },
    ...overrides,
  };
}

function document(operations = [route()]) {
  const paths = {};
  for (const operation of operations) {
    paths[operation.path] ??= {};
    paths[operation.path][operation.method.toLowerCase()] = {
      operationId: operation.operationId,
      "x-devfeed-gateway": structuredClone(operation.policy),
      parameters: operation.parameters.map(({ name, schema }) => ({
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
    "x-devfeed-gateway-routes": structuredClone(operations),
  };
}

function product(overrides = {}) {
  return route({
    operationId: "partnerProductGet",
    path: "/v1/partner/accounts/{account_id}/products/{product_id}",
    parameters: [
      { name: "account_id", converter: "str", schema: uuid },
      { name: "product_id", converter: "uuid", schema: uuid },
    ],
    ...overrides,
  });
}

async function sandbox(callback) {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-partner-routes-"));
  const options = {
    source: join(directory, "openapi.json"),
    target: join(directory, "generated/partner-operations.ts"),
    routesRoot: join(directory, "api"),
  };
  try {
    await writeFile(options.source, JSON.stringify(document()));
    await callback(options, directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("concrete methods bind stable operation identities and original backend parameter names", async () => {
  const input = document([
    product(),
    product({
      operationId: "partnerProductUpdate",
      method: "PATCH",
      policy: { ...policy, bodyLimit: 65536 },
    }),
    product({ operationId: "partnerProductArchive", method: "DELETE" }),
  ]);
  const result = await renderPartnerRoutes(input);
  assert.deepEqual(
    [...result.routes.keys()],
    ["accounts/[account_id]/products/[product_id]/route.ts"],
  );
  const source = [...result.routes.values()][0];
  for (const [method, id] of [
    ["GET", "partnerProductGet"],
    ["PATCH", "partnerProductUpdate"],
    ["DELETE", "partnerProductArchive"],
  ]) {
    assert.match(source, new RegExp(`export const ${method} = bindPartnerOperation`));
    assert.ok(source.includes(`partnerGatewayOperations["${id}"]`));
  }
  assert.match(source, /rejectPartnerMethod\(\["GET", "PATCH", "DELETE"\]\)/);
  assert.match(source, /^ \* OpenAPI spec version: 1\.2\.3$/m);
  assert.match(result.operations, /bodyLimit: 65536/);
  assert.match(result.operations, /as const satisfies Readonly<Record<string, GatewayOperation>>/);
  assert.equal(source.includes("gateway(request"), false);
});

test("GET-only routes reject implicit HEAD, while explicit backend HEAD and native OPTIONS stay intact", async () => {
  const generated = await renderPartnerRoutes(document());
  const getOnly = generated.routes.get("accounts/route.ts");
  assert.match(getOnly, /export const HEAD = rejectPartnerMethod\(\["GET"\]\)/);
  assert.equal(getOnly.includes("export const OPTIONS"), false);
  const explicitHead = await renderPartnerRoutes(
    document([route(), route({ operationId: "partnerAccountsHead", method: "HEAD" })]),
  );
  const headSource = explicitHead.routes.get("accounts/route.ts");
  assert.match(headSource, /export const HEAD = bindPartnerOperation/);
  assert.equal(headSource.includes("rejectPartnerMethod"), false);
  const writeOnly = await renderPartnerRoutes(
    document([route({ operationId: "partnerAccountCreate", method: "POST" })]),
  );
  assert.equal(writeOnly.routes.get("accounts/route.ts").includes("export const HEAD"), false);
});

test("terminal backend path converters generate a required Next catch-all", async () => {
  const result = await renderPartnerRoutes(
    document([
      route({
        operationId: "partnerFutureInbox",
        path: "/v1/partner/notifications/{path}",
        parameters: [{ name: "path", converter: "path", schema: { type: "string" } }],
      }),
    ]),
  );
  assert.deepEqual([...result.routes.keys()], ["notifications/[...path]/route.ts"]);
  assert.match(result.operations, /converter: "path"/);
});

test("Next dynamic filename conflicts are rejected even when the leaves or methods differ", () => {
  assert.throws(
    () =>
      partnerRoutePlan(
        document([
          product(),
          route({
            operationId: "partnerPreferencesUpdate",
            method: "PUT",
            path: "/v1/partner/accounts/{identifier}/preferences",
            parameters: [{ name: "identifier", converter: "str", schema: uuid }],
          }),
        ]),
      ),
    /Conflicting Next dynamic route names/,
  );
  assert.throws(
    () =>
      partnerRoutePlan(
        document([
          route({
            path: "/v1/partner/files/{path}",
            parameters: [{ name: "path", converter: "path", schema: { type: "string" } }],
          }),
          route({
            operationId: "partnerFileWrite",
            method: "POST",
            path: "/v1/partner/files/{name}",
            parameters: [{ name: "name", converter: "str", schema: { type: "string" } }],
          }),
        ]),
      ),
    /Conflicting Next dynamic route names/,
  );
});

test("one concrete route retains loose GET and strict PATCH parameter contracts independently", () => {
  const loose = product({
    parameters: [
      { name: "account_id", converter: "str", schema: uuid },
      { name: "product_id", converter: "str", schema: { type: "string" } },
    ],
  });
  const strict = product({
    operationId: "partnerProductUpdate",
    method: "PATCH",
    parameters: [
      { name: "account_id", converter: "str", schema: { ...uuid, maxLength: 36 } },
      { name: "product_id", converter: "uuid", schema: uuid },
    ],
  });
  const plan = partnerRoutePlan(document([loose, strict]));
  assert.equal(plan.groups.length, 1);
  assert.equal(plan.groups[0].filename, "accounts/[account_id]/products/[product_id]/route.ts");
  assert.deepEqual(plan.groups[0].operations[0].parameters, loose.parameters);
  assert.deepEqual(plan.groups[0].operations[1].parameters, strict.parameters);
});

test("different backend converters cannot create duplicate GET exports in one Next route", () => {
  assert.throws(
    () =>
      partnerRoutePlan(
        document([
          product(),
          product({
            operationId: "partnerProductLooseGet",
            parameters: [
              { name: "account_id", converter: "str", schema: uuid },
              { name: "product_id", converter: "str", schema: { type: "string" } },
            ],
          }),
        ]),
      ),
    /Duplicate concrete GET operation/,
  );
});

test("duplicate stable operation identities cannot overwrite generated map entries", () => {
  assert.throws(
    () => partnerRoutePlan(document([route(), route({ method: "POST" })])),
    /Duplicate partner operation identity/,
  );
});

test("unsafe namespaces, unsupported Next syntax and missing policies fail before generation", () => {
  for (const operation of [
    route({ path: "/v1/admin/accounts" }),
    route({ path: "/v1/partner/(private)/accounts" }),
    route({ path: "/v1/partner/@slot/accounts" }),
    route({ path: "/v1/partner/%2e%2e/accounts" }),
    route({ policy: undefined }),
  ]) {
    assert.throws(() => partnerRoutePlan(document([operation])));
  }
});

test("operation map computed keys cannot become object prototype setters or executable source", async () => {
  for (const operationId of ["__proto__", 'config\"]; throw new Error("injected"); //']) {
    const result = await renderPartnerRoutes(document([route({ operationId })]));
    const context = { module: { exports: {} } };
    runInNewContext(
      transformSync(result.operations, { loader: "ts", format: "cjs" }).code,
      context,
    );
    const operations = context.module.exports.partnerGatewayOperations;
    assert.equal(Object.hasOwn(operations, operationId), true);
    assert.equal(operations[operationId].operationId, operationId);
    assert.equal(Object.keys(operations).length, 1);
    assert.doesNotThrow(() =>
      transformSync(result.routes.get("accounts/route.ts"), { loader: "ts", format: "cjs" }),
    );
  }
});

test("generation is deterministic and spec-version validation rejects injected headers", async () => {
  const value = document([route(), product()]);
  const before = structuredClone(value);
  const first = await renderPartnerRoutes(value);
  value.paths = Object.fromEntries(Object.entries(value.paths).reverse());
  const second = await renderPartnerRoutes(value);
  assert.deepEqual(second, first);
  assert.deepEqual(value["x-devfeed-gateway-routes"], before["x-devfeed-gateway-routes"]);
  value.info.version = "1.2.3\n */\nthrow new Error('injected')";
  await assert.rejects(renderPartnerRoutes(value), /release version/);
});

test("stale checks detect changed, missing and obsolete routes without rewriting files", async () => {
  await sandbox(async (options) => {
    await generatePartnerRoutes(options);
    await generatePartnerRoutes({ ...options, check: true });
    const filename = join(options.routesRoot, "accounts/route.ts");
    const original = await readFile(filename, "utf8");
    await writeFile(filename, original + "// stale\n");
    await assert.rejects(generatePartnerRoutes({ ...options, check: true }), /stale/);
    assert.equal(await readFile(filename, "utf8"), original + "// stale\n");
    await writeFile(filename, original);
    const obsolete = join(options.routesRoot, "obsolete/route.ts");
    await mkdir(dirname(obsolete), { recursive: true });
    await writeFile(obsolete, original);
    await assert.rejects(generatePartnerRoutes({ ...options, check: true }), /stale/);
    assert.equal(await readFile(obsolete, "utf8"), original);
    await rm(filename);
    await assert.rejects(generatePartnerRoutes({ ...options, check: true }), /stale/);
  });
});

test("only files owned by this generator are cleaned when a contract removes routes", async () => {
  await sandbox(async (options) => {
    await generatePartnerRoutes(options);
    const handwritten = join(options.routesRoot, "handwritten/route.ts");
    await mkdir(dirname(handwritten), { recursive: true });
    await writeFile(handwritten, "export const GET = () => new Response('manual');\n");
    await writeFile(
      options.source,
      JSON.stringify(
        document([route({ operationId: "partnerDashboard", path: "/v1/partner/dashboard" })]),
      ),
    );
    await generatePartnerRoutes(options);
    await assert.rejects(readFile(join(options.routesRoot, "accounts/route.ts")), {
      code: "ENOENT",
    });
    assert.match(await readFile(handwritten, "utf8"), /manual/);
    await generatePartnerRoutes({ ...options, check: true });
  });
});

test("generation refuses handwritten collisions before writing any operation or route", async () => {
  await sandbox(async (options) => {
    const filename = join(options.routesRoot, "accounts/route.ts");
    await mkdir(dirname(filename), { recursive: true });
    await writeFile(filename, "handwritten\n");
    await assert.rejects(generatePartnerRoutes(options), /handwritten partner route/);
    assert.equal(await readFile(filename, "utf8"), "handwritten\n");
    await assert.rejects(readFile(options.target), { code: "ENOENT" });
    await rm(filename);
    await mkdir(dirname(options.target), { recursive: true });
    await writeFile(options.target, "handwritten operation\n");
    await assert.rejects(generatePartnerRoutes(options), /handwritten partner operation/);
    assert.equal(await readFile(options.target, "utf8"), "handwritten operation\n");
  });
});

test("handwritten dynamic directory conflicts and symlinks cannot redirect generated writes", async () => {
  await sandbox(async (options, directory) => {
    await writeFile(options.source, JSON.stringify(document([product()])));
    const filename = join(options.routesRoot, "accounts/[another_account]/manual/route.ts");
    await mkdir(dirname(filename), { recursive: true });
    await writeFile(filename, "handwritten\n");
    await assert.rejects(generatePartnerRoutes(options), /Conflicting Next dynamic route names/);
    await rm(options.routesRoot, { recursive: true });
    const outside = join(directory, "outside");
    await mkdir(outside);
    await mkdir(options.routesRoot);
    await symlink(outside, join(options.routesRoot, "accounts"));
    await assert.rejects(generatePartnerRoutes(options), /symlinked partner route/);
    await assert.rejects(readFile(options.target), { code: "ENOENT" });
  });
});

test("checked-in concrete handlers and descriptors match the authoritative partner export", async () => {
  await generatePartnerRoutes({ check: true });
});

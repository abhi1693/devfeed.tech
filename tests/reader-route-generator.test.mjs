import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import {
  generateReaderRoutes,
  readerRoutePlan,
  renderReaderRoutes,
} from "../scripts/generate_reader_routes.mjs";
import { gatewayOperations } from "../scripts/lib/gateway-contract.mjs";

const ordinary = {
  bodyLimit: 1_000_000,
  bodyTimeoutMs: 2000,
  upstreamTimeoutMs: 45_000,
  cookiePolicy: "reader",
  handler: "forward",
  extension: null,
};

function route(overrides = {}) {
  return {
    operationId: "user_auth_me",
    method: "GET",
    path: "/v1/user/auth/me",
    parameters: [],
    policy: { ...ordinary },
    ...overrides,
  };
}

function placement(overrides = {}) {
  return route({
    operationId: "placements_v1_user_partner_placements_query_post",
    method: "POST",
    path: "/v1/user/partner-placements/query",
    policy: { ...ordinary, handler: "placement-query", bodyLimit: 8192 },
    ...overrides,
  });
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

test("every reader descriptor retains explicit cookie and body policies without path inference", async () => {
  const login = route({
    operationId: "user_auth_login",
    path: "/v1/user/auth/login",
    policy: { ...ordinary, cookiePolicy: "reader-attribution" },
  });
  const avatar = route({
    operationId: "reader_avatar_upload",
    method: "POST",
    path: "/v1/user/settings/profile/avatar",
    policy: { ...ordinary, bodyLimit: 5 * 1024 * 1024 + 65536, bodyTimeoutMs: 10_000 },
  });
  const plan = readerRoutePlan(document([login, avatar]));
  assert.equal(plan.operations[0].policy.cookiePolicy, "reader-attribution");
  assert.equal(plan.operations[1].policy.bodyLimit, 5 * 1024 * 1024 + 65536);
  assert.equal(plan.operations[1].policy.bodyTimeoutMs, 10_000);
  const renamed = { ...avatar, path: "/v1/user/renamed-artwork" };
  assert.deepEqual(readerRoutePlan(document([renamed])).operations[0].policy, avatar.policy);
  const source = await renderReaderRoutes(document([login]));
  assert.match(source.operations, /import type \{ ReaderGatewayOperation \}/);
  assert.match(source.operations, /userGatewayOperations/);
});

test("placement validation is bound exclusively to the dedicated factory on its concrete POST route", async () => {
  const result = await renderReaderRoutes(document([route(), placement()]));
  const personalized = result.routes.get("partner-placements/query/route.ts");
  assert.match(
    personalized,
    /bindPersonalizedPartnerPlacements.*@\/lib\/server\/partner-placements/,
  );
  assert.match(personalized, /export const POST = bindPersonalizedPartnerPlacements/);
  assert.equal(personalized.includes("bindReaderOperation"), false);
  assert.equal(personalized.includes("validPlacementQuery"), false);
  assert.match(result.routes.get("auth/me/route.ts"), /bindReaderOperation/);
  assert.equal(
    result.routes.get("auth/me/route.ts").includes("bindPersonalizedPartnerPlacements"),
    false,
  );
});

test("handler metadata selects the dedicated factory even when a placement route is renamed", async () => {
  const result = await renderReaderRoutes(
    document([placement({ path: "/v1/user/renamed-recommendations" })]),
  );
  assert.match(
    result.routes.get("renamed-recommendations/route.ts"),
    /bindPersonalizedPartnerPlacements/,
  );
  const generic = await renderReaderRoutes(
    document([placement({ policy: { ...ordinary, bodyLimit: 8192 } })]),
  );
  assert.match(generic.routes.get("partner-placements/query/route.ts"), /bindReaderOperation/);
  assert.equal(
    generic.routes
      .get("partner-placements/query/route.ts")
      .includes("bindPersonalizedPartnerPlacements"),
    false,
  );
});

test("placement handler cannot be declared on GET, PUT, DELETE or HEAD", () => {
  for (const method of ["GET", "PUT", "DELETE", "HEAD"]) {
    assert.throws(() => readerRoutePlan(document([placement({ method })])), /requires POST/);
  }
});

test("missing or unknown reader cookie and handler fields fail instead of guessing policies", () => {
  for (const field of ["cookiePolicy", "handler"]) {
    for (const value of [undefined, null, "unknown", 1]) {
      assert.throws(() =>
        readerRoutePlan(document([route({ policy: { ...ordinary, [field]: value } })])),
      );
    }
    const incomplete = { ...ordinary };
    delete incomplete[field];
    assert.throws(() => readerRoutePlan(document([route({ policy: incomplete })])));
  }
});

test("reader-specific fields cannot silently broaden admin or partner policies", () => {
  for (const namespace of ["admin", "partner"]) {
    assert.throws(
      () =>
        gatewayOperations(document([route({ path: `/v1/${namespace}/auth/me` })]), { namespace }),
      /unsupported field cookiePolicy/,
    );
  }
});

test("documented cookie and handler declarations must agree with the ordered export", () => {
  for (const [field, value] of [
    ["cookiePolicy", "omit"],
    ["handler", "placement-query"],
  ]) {
    const input = document([placement()]);
    input.paths["/v1/user/partner-placements/query"].post["x-devfeed-gateway"][field] = value;
    if (field === "handler")
      input.paths["/v1/user/partner-placements/query"].post["x-devfeed-gateway"].handler =
        "forward";
    assert.throws(() => readerRoutePlan(input), /Gateway policy differs/);
  }
});

test("all supported cookie policies serialize unchanged including explicitly omitted cookies", () => {
  for (const cookiePolicy of ["reader", "reader-attribution", "omit"]) {
    const plan = readerRoutePlan(document([route({ policy: { ...ordinary, cookiePolicy } })]));
    assert.equal(plan.operations[0].policy.cookiePolicy, cookiePolicy);
  }
});

test("hidden inbox methods generate one required catch-all while HEAD never becomes an implicit GET", async () => {
  const input = document();
  input["x-devfeed-gateway-routes"].push(
    ...["GET", "POST", "PUT"].map((method) =>
      route({
        operationId: `user_inbox_${method.toLowerCase()}`,
        method,
        path: "/v1/user/notifications/chimely/v1/inbox/{path}",
        parameters: [{ name: "path", converter: "path", schema: { type: "string" } }],
      }),
    ),
  );
  const result = await renderReaderRoutes(input);
  const source = result.routes.get("notifications/chimely/v1/inbox/[...path]/route.ts");
  assert.match(source, /export const GET = bindReaderOperation/);
  assert.match(source, /export const POST = bindReaderOperation/);
  assert.match(source, /export const PUT = bindReaderOperation/);
  assert.match(source, /export const HEAD = rejectReaderMethod\(\["GET", "POST", "PUT"\]\)/);
  assert.equal(source.includes("export const OPTIONS"), false);
});

test("reader generation preserves schemas, version and deterministic route bytes", async () => {
  const input = document([route(), placement()]);
  const snapshot = structuredClone(input);
  const first = await renderReaderRoutes(input);
  input.paths = Object.fromEntries(Object.entries(input.paths).reverse());
  assert.deepEqual(await renderReaderRoutes(input), first);
  assert.deepEqual(input["x-devfeed-gateway-routes"], snapshot["x-devfeed-gateway-routes"]);
  assert.match(first.operations, /^ \* OpenAPI spec version: 1\.2\.3$/m);
  input.info.version = "1.2.3\n */\nthrow new Error('injected')";
  await assert.rejects(renderReaderRoutes(input), /release version/);
});

test("isolated reader generation detects stale output and cleans only its marked routes", async () => {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-reader-routes-"));
  const options = {
    source: join(directory, "openapi.json"),
    target: join(directory, "generated/user-operations.ts"),
    routesRoot: join(directory, "api"),
  };
  try {
    await writeFile(options.source, JSON.stringify(document()));
    await generateReaderRoutes(options);
    await generateReaderRoutes({ ...options, check: true });
    const hand = join(options.routesRoot, "manual/route.ts");
    await mkdir(dirname(hand), { recursive: true });
    await writeFile(hand, "handwritten\n");
    const generated = join(options.routesRoot, "auth/me/route.ts");
    const original = await readFile(generated, "utf8");
    await writeFile(generated, original + "// stale\n");
    await assert.rejects(generateReaderRoutes({ ...options, check: true }), /npm run web:generate/);
    assert.equal(await readFile(generated, "utf8"), original + "// stale\n");
    await writeFile(options.source, JSON.stringify(document([placement()])));
    await generateReaderRoutes(options);
    await assert.rejects(readFile(generated), { code: "ENOENT" });
    assert.equal(await readFile(hand, "utf8"), "handwritten\n");
    await generateReaderRoutes({ ...options, check: true });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("checked-in reader handlers and policies match the authoritative user API export", async () => {
  await generateReaderRoutes({ check: true });
});

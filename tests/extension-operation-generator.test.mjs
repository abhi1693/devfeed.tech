import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import {
  extensionOperationPlan,
  generateExtensionOperations,
  renderExtensionOperations,
} from "../scripts/generate_extension_operations.mjs";
import { extensionTransportPolicy, gatewayOperations } from "../scripts/lib/gateway-contract.mjs";

test("committed extension operation registry matches current contracts", async () => {
  await assert.doesNotReject(() => generateExtensionOperations({ check: true }));
});

const extension = {
  credentials: "include",
  cacheControl: null,
  requestHeaders: ["Content-Type", "X-CSRF-Token", "If-None-Match"],
  timeoutMs: 15_000,
};
const bounds = { bodyLimit: 1_000_000, bodyTimeoutMs: 2000, upstreamTimeoutMs: 45_000 };
const ordinary = { ...bounds, cookiePolicy: "reader", handler: "forward", extension };

function operation(overrides = {}) {
  return {
    operationId: "user_me",
    method: "GET",
    path: "/v1/user/auth/me",
    parameters: [],
    policy: structuredClone(ordinary),
    ...overrides,
  };
}

function document(operations = [operation()]) {
  const paths = {};
  for (const item of operations) {
    paths[item.path] ??= {};
    paths[item.path][item.method.toLowerCase()] = {
      operationId: item.operationId,
      "x-devfeed-gateway": structuredClone(item.policy),
      parameters: item.parameters.map(({ name, schema }) => ({ name, in: "path", schema })),
    };
  }
  return {
    openapi: "3.1.0",
    info: { version: "1.2.3" },
    paths,
    "x-devfeed-gateway-routes": structuredClone(operations),
    "x-devfeed-public-operations": [
      { operationId: "public_feed", method: "GET", path: "/v1/feed" },
      { operationId: "public_article", method: "GET", path: "/v1/articles/{article_id}" },
      { operationId: "public_health", method: "GET", path: "/health" },
    ],
  };
}

function binding(overrides = {}) {
  return {
    operationId: "readerFeed",
    routeFile: "v1/feed/route.ts",
    method: "GET",
    parameters: [],
    backendOperations: ["public_feed", "user_me"],
    policy: {
      ...bounds,
      extension: { ...extension, cacheControl: "public-read", requestHeaders: [] },
    },
    ...overrides,
  };
}

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-extension-operations-"));
  const options = {
    source: join(directory, "openapi.json"),
    bindings: join(directory, "reader-bindings.json"),
    apiRoot: join(directory, "api"),
    target: join(directory, "generated/extension-operations.ts"),
  };
  const writeRoute = async (
    routeFile,
    content = "export async function GET() { return Response.json({}); }",
  ) => {
    const destination = join(options.apiRoot, routeFile);
    await mkdir(dirname(destination), { recursive: true });
    await writeFile(destination, content);
  };
  try {
    await writeRoute("v1/feed/route.ts");
    await run(options, writeRoute);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("extension policy requires explicit eligibility and never invents credentials or cache behavior", () => {
  for (const field of ["credentials", "cacheControl", "requestHeaders", "timeoutMs"]) {
    const incomplete = structuredClone(extension);
    delete incomplete[field];
    assert.throws(() => extensionTransportPolicy(incomplete));
  }
  const missing = { ...ordinary };
  delete missing.extension;
  assert.throws(
    () => gatewayOperations(document([operation({ policy: missing })]), { namespace: "user" }),
    /explicitly declare/,
  );
  assert.equal(
    gatewayOperations(document([operation({ policy: { ...ordinary, extension: null } })]), {
      namespace: "user",
    })[0].policy.extension,
    null,
  );
});

test("only declared safe request headers and finite deadlines can be generated", () => {
  for (const requestHeaders of [
    ["Authorization"],
    ["Cookie"],
    ["Origin"],
    ["content-type"],
    ["Content-Type", "Content-Type"],
    "Content-Type",
  ]) {
    assert.throws(
      () => extensionTransportPolicy({ ...extension, requestHeaders }),
      /supported headers/,
    );
  }
  for (const timeoutMs of [0, -1, Infinity, 1.5, "15000", Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(
      () => extensionTransportPolicy({ ...extension, timeoutMs }),
      /positive safe integer/,
    );
  }
  for (const credentials of ["same-origin", null, "omit "])
    assert.throws(() => extensionTransportPolicy({ ...extension, credentials }));
  for (const cacheControl of ["max-age=600", "private", undefined])
    assert.throws(() => extensionTransportPolicy({ ...extension, cacheControl }));
  assert.throws(
    () => extensionTransportPolicy({ ...extension, authorization: true }),
    /unsupported field/,
  );
});

test("enabled private operations reuse exact identities and policies while denied OAuth is absent", async () => {
  await fixture(async (options) => {
    const login = operation({
      operationId: "user_login",
      path: "/v1/user/auth/login",
      policy: { ...ordinary, cookiePolicy: "reader-attribution", extension: null },
    });
    const avatar = operation({
      operationId: "avatar_delete",
      method: "DELETE",
      path: "/v1/user/settings/profile/avatar",
      policy: { ...ordinary, extension: { ...extension, timeoutMs: 45_000 } },
    });
    const input = document([operation(), login, avatar]);
    const result = await extensionOperationPlan(input, { bindings: [] }, options);
    assert.deepEqual(
      result.privateOperations.map(({ operationId }) => operationId),
      ["user_me", "avatar_delete"],
    );
    assert.equal(result.privateOperations[1].policy.extension.timeoutMs, 45_000);
    assert.equal(result.privateOperations[1].path, "/api/v1/user/settings/profile/avatar");
    const output = await renderExtensionOperations(input, { bindings: [] }, options);
    assert.match(output, /\.\.\.userGatewayOperations\["user_me"\]/);
    assert.equal(output.includes("user_login"), false);
    assert.equal(output.includes("public_health"), false);
    assert.match(output, /OpenAPI spec version: 1\.2\.3/);
  });
});

test("public adapters derive paths from Next files and preserve composite backend provenance", async () => {
  await fixture(async (options, writeRoute) => {
    await writeRoute("v1/articles/[slug]/route.ts", "export const GET = () => new Response();");
    const parameters = [
      {
        name: "slug",
        converter: "str",
        schema: { type: "string", minLength: 1, maxLength: 200, pattern: "^[a-z0-9-]+$" },
      },
    ];
    const item = binding({
      operationId: "readerArticle",
      routeFile: "v1/articles/[slug]/route.ts",
      parameters,
      backendOperations: ["public_article", "user_me"],
    });
    const result = await extensionOperationPlan(document(), { bindings: [item] }, options);
    assert.equal(result.publicOperations[0].path, "/api/v1/articles/{slug}");
    assert.deepEqual(result.publicOperations[0].parameters, parameters);
    assert.deepEqual(result.publicOperations[0].policy.extension, item.policy.extension);
    assert.equal("backendOperations" in result.publicOperations[0], false);
  });
});

test("frontend configuration can be explicitly bound without a backend operation", async () => {
  await fixture(async (options, writeRoute) => {
    await writeRoute("v1/mcp/config/route.ts");
    const item = binding({
      operationId: "readerMcpConfig",
      routeFile: "v1/mcp/config/route.ts",
      backendOperations: [],
      policy: {
        ...bounds,
        extension: { ...extension, cacheControl: "no-store", requestHeaders: [] },
      },
    });
    const result = await extensionOperationPlan(document(), { bindings: [item] }, options);
    assert.equal(result.publicOperations[0].policy.extension.cacheControl, "no-store");
  });
});

test("method verification recognizes named exports and aliases without executing route modules", async () => {
  await fixture(async (options, writeRoute) => {
    for (const content of [
      "import 'server-only'; export async function POST() { throw Error('must never run'); }",
      "export const POST = unrelatedServerHandler;",
      "export { searchClick as POST } from '@/lib/server/search-click';",
      "const actual = () => {}; export { actual as POST };",
    ]) {
      await writeRoute("v1/feed/route.ts", content);
      const result = await extensionOperationPlan(
        document(),
        { bindings: [binding({ method: "POST" })] },
        options,
      );
      assert.equal(result.publicOperations[0].method, "POST");
    }
  });
});

test("comments, type exports, unexported declarations, wildcard exports and malformed source do not authorize methods", async () => {
  await fixture(async (options, writeRoute) => {
    for (const content of [
      "// export function POST() {}",
      "function POST() {}",
      "export default function POST() {}",
      "export type { POST } from './types';",
      "export { type POST } from './types';",
      "export * from './handlers';",
      "export const POST = ;",
    ]) {
      await writeRoute("v1/feed/route.ts", content);
      await assert.rejects(
        extensionOperationPlan(document(), { bindings: [binding({ method: "POST" })] }, options),
        /does not export POST|Cannot parse/,
      );
    }
  });
});

test("unknown references, catalog collisions and missing provenance arrays fail generation", async () => {
  await fixture(async (options) => {
    for (const backendOperations of [["unrecognized"], ["public_feed", "public_feed"], undefined]) {
      await assert.rejects(
        extensionOperationPlan(document(), { bindings: [binding({ backendOperations })] }, options),
        /backend operation/,
      );
    }
    const input = document();
    input["x-devfeed-public-operations"][0].operationId = "user_me";
    await assert.rejects(
      extensionOperationPlan(input, { bindings: [] }, options),
      /Duplicate backend identity/,
    );
    delete input["x-devfeed-public-operations"];
    await assert.rejects(
      extensionOperationPlan(input, { bindings: [] }, options),
      /Missing public/,
    );
  });
});

test("public bindings must declare non-null extension policy and valid bounded bodies", async () => {
  await fixture(async (options) => {
    for (const policy of [
      { ...bounds },
      { ...bounds, extension: null },
      { ...bounds, extension, bodyLimit: 0 },
      { ...bounds, extension, bodyTimeoutMs: Infinity },
      { ...bounds, extension, handler: "forward" },
    ]) {
      await assert.rejects(
        extensionOperationPlan(document(), { bindings: [binding({ policy })] }, options),
      );
    }
  });
});

test("route files cannot escape the API tree or use unsupported Next route constructs", async () => {
  await fixture(async (options) => {
    for (const routeFile of [
      "../route.ts",
      "/v1/feed/route.ts",
      "v1/../feed/route.ts",
      "v1/feed%2Froute.ts",
      "v1/(group)/route.ts",
      "v1/[[...optional]]/route.ts",
      "v1/@parallel/route.ts",
      "v1/[...path]/tail/route.ts",
      "v1/feed/page.ts",
    ]) {
      await assert.rejects(
        extensionOperationPlan(document(), { bindings: [binding({ routeFile })] }, options),
        /relative|unsupported|terminal/,
      );
    }
    await assert.rejects(
      extensionOperationPlan(
        document(),
        { bindings: [binding({ routeFile: "v1/missing/route.ts" })] },
        options,
      ),
      /ENOENT/,
    );
  });
});

test("symlinked route files are never treated as declared public surfaces", async () => {
  await fixture(async (options) => {
    const destination = join(options.apiRoot, "v1/linked");
    await symlink(join(options.apiRoot, "v1/feed"), destination);
    await assert.rejects(
      extensionOperationPlan(
        document(),
        { bindings: [binding({ routeFile: "v1/linked/route.ts" })] },
        options,
      ),
      /symlinked/,
    );
  });
});

test("public parameter constraints must match every actual Next dynamic name and converter", async () => {
  await fixture(async (options, writeRoute) => {
    await writeRoute("v1/items/[slug]/route.ts");
    for (const parameters of [
      [],
      [{ name: "wrong", converter: "str", schema: { type: "string" } }],
      [{ name: "slug", converter: "path", schema: { type: "string" } }],
      [{ name: "slug", converter: "uuid", schema: { type: "string" } }],
      [{ name: "slug", converter: "str", schema: { type: "number" } }],
    ]) {
      await assert.rejects(
        extensionOperationPlan(
          document(),
          { bindings: [binding({ routeFile: "v1/items/[slug]/route.ts", parameters })] },
          options,
        ),
        /parameter|UUID/,
      );
    }
    await writeRoute("v1/items/[...path]/route.ts");
    const item = binding({
      routeFile: "v1/items/[...path]/route.ts",
      parameters: [{ name: "path", converter: "path", schema: { type: "string" } }],
    });
    assert.equal(
      (await extensionOperationPlan(document(), { bindings: [item] }, options)).publicOperations[0]
        .parameters[0].converter,
      "path",
    );
  });
});

test("operation and route conflicts fail instead of choosing the first declaration", async () => {
  await fixture(async (options, writeRoute) => {
    await assert.rejects(
      extensionOperationPlan(document(), { bindings: [binding(), binding()] }, options),
      /Duplicate reader identity/,
    );
    await assert.rejects(
      extensionOperationPlan(
        document(),
        { bindings: [binding(), binding({ operationId: "readerOther" })] },
        options,
      ),
      /Duplicate public reader route/,
    );
    await assert.rejects(
      extensionOperationPlan(
        document(),
        { bindings: [binding({ operationId: "user_me" })] },
        options,
      ),
      /Duplicate reader identity/,
    );
    await writeRoute("v1/user/auth/me/route.ts");
    await assert.rejects(
      extensionOperationPlan(
        document(),
        { bindings: [binding({ routeFile: "v1/user/auth/me/route.ts" })] },
        options,
      ),
      /Conflicting private\/public/,
    );
    for (const name of ["slug", "id"])
      await writeRoute(
        `v1/items/[${name}]/route.ts`,
        "export const GET = () => {}; export const POST = () => {};",
      );
    const items = ["slug", "id"].map((name, index) =>
      binding({
        operationId: `readerItem${index}`,
        routeFile: `v1/items/[${name}]/route.ts`,
        method: index ? "POST" : "GET",
        parameters: [{ name, converter: "str", schema: { type: "string" } }],
      }),
    );
    await assert.rejects(
      extensionOperationPlan(document(), { bindings: items }, options),
      /Conflicting public Next route names/,
    );
  });
});

test("generation is deterministic, checks stale output and leaves public route source untouched", async () => {
  await fixture(async (options) => {
    const input = document();
    const declaration = { bindings: [binding()] };
    const route = await readFile(join(options.apiRoot, "v1/feed/route.ts"), "utf8");
    await writeFile(options.source, JSON.stringify(input));
    await writeFile(options.bindings, JSON.stringify(declaration));
    await generateExtensionOperations(options);
    await generateExtensionOperations({ ...options, check: true });
    const first = await readFile(options.target, "utf8");
    await generateExtensionOperations(options);
    assert.equal(await readFile(options.target, "utf8"), first);
    assert.equal(await readFile(join(options.apiRoot, "v1/feed/route.ts"), "utf8"), route);
    declaration.bindings[0].policy.extension.credentials = "omit";
    await writeFile(options.bindings, JSON.stringify(declaration));
    await assert.rejects(generateExtensionOperations({ ...options, check: true }), /stale/);
    assert.equal(await readFile(options.target, "utf8"), first);
    await writeFile(options.target, "export const handwritten = true;\n");
    await assert.rejects(generateExtensionOperations(options), /handwritten/);
  });
});

test("invalid contract versions cannot inject code into the generated header", async () => {
  await fixture(async (options) => {
    const input = document();
    input.info.version = "1.2.3\n */\nthrow Error('injection');";
    await assert.rejects(
      renderExtensionOperations(input, { bindings: [] }, options),
      /release version/,
    );
  });
});

test("repository registry exposes exactly the declared private and public adapter inventory", async () => {
  const input = JSON.parse(
    await readFile(new URL("../apps/web/openapi.json", import.meta.url), "utf8"),
  );
  const declaration = JSON.parse(
    await readFile(
      new URL("../apps/web/src/lib/api/reader-bindings.json", import.meta.url),
      "utf8",
    ),
  );
  const plan = await extensionOperationPlan(input, declaration);
  assert.equal(plan.privateOperations.length, 59);
  assert.equal(plan.publicOperations.length, 14);
  assert.equal(plan.privateOperations.length + plan.publicOperations.length, 73);
  const inboxPrefix = "/api/v1/user/notifications/chimely/v1/inbox/";
  const inbox = plan.privateOperations.filter(({ path }) => path.startsWith(inboxPrefix));
  const inboxOperations = [
    ["Items", "GET", "items", []],
    ["Counts", "GET", "counts", []],
    ["PreferencesRead", "GET", "preferences", []],
    ["PreferencesUpdate", "PUT", "preferences", []],
    ["Stream", "GET", "stream", []],
    ["ReadAll", "POST", "read-all", []],
    ["SeenAll", "POST", "seen-all", []],
    ["ArchiveAll", "POST", "archive-all", []],
    ["ArchiveRead", "POST", "archive-read", []],
    ...["Notification", "Broadcast"].flatMap((kind) => {
      const parameter = `${kind.toLowerCase()}_id`;
      return ["Read", "Unread", "Archive", "Unarchive"].map((action) => [
        `${kind}${action}`,
        "POST",
        `${kind.toLowerCase()}s/{${parameter}}/${action.toLowerCase()}`,
        [parameter],
      ]);
    }),
  ];
  assert.equal(inbox.length, 17);
  assert.deepEqual(
    inbox.map(({ operationId, method, path, parameters }) => [
      operationId,
      method,
      path,
      parameters.map(({ name }) => name),
    ]),
    inboxOperations.map(([name, method, suffix, parameters]) => [
      `userInbox${name}`,
      method,
      `${inboxPrefix}${suffix}`,
      parameters,
    ]),
  );
  for (const { parameters } of inbox) {
    for (const parameter of parameters) {
      assert.equal(parameter.converter, "str");
      assert.deepEqual(parameter.schema, {
        type: "string",
        minLength: 32,
        maxLength: 32,
        pattern: `^${parameter.name === "notification_id" ? "notif" : "bcast"}_[a-z0-9]{26}$`,
      });
    }
  }
  const privateIds = new Set(plan.privateOperations.map(({ operationId }) => operationId));
  for (const item of input["x-devfeed-gateway-routes"].filter(
    ({ policy }) => policy.extension === null,
  ))
    assert.equal(privateIds.has(item.operationId), false);
  const avatar = plan.privateOperations.filter(({ path }) =>
    path.endsWith("/settings/profile/avatar"),
  );
  assert.equal(avatar.length, 2);
  assert.deepEqual(
    avatar.map(({ policy }) => policy.extension.timeoutMs),
    [45_000, 45_000],
  );
  assert.equal(
    plan.publicOperations.some(
      ({ path }) =>
        path.endsWith("/health") || path.endsWith("/version") || path.endsWith("/analytics"),
    ),
    false,
  );
});

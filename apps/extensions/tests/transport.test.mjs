import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { build } from "esbuild";

async function compile(relativePath) {
  const result = await build({
    entryPoints: [new URL(relativePath, import.meta.url).pathname],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
  });
  return result.outputFiles[0].text;
}

const [transportSource, operationSource] = await Promise.all([
  compile("../src/transport.ts"),
  compile("../../web/src/lib/api/generated/extension-operations.ts"),
]);
const operationModule = { exports: {} };
runInNewContext(operationSource, { module: operationModule, exports: operationModule.exports });
// Plain fixtures avoid sharing a VM's array/object prototypes with node:assert.
const operations = JSON.parse(JSON.stringify(operationModule.exports.extensionGatewayOperations));
const origin = "https://devfeed.tech";
const params = {
  article_id: "11111111-1111-4111-8111-111111111111",
  topic_id: "22222222-2222-4222-8222-222222222222",
  tag_id: "55555555-5555-4555-8555-555555555555",
  source_id: "33333333-3333-4333-8333-333333333333",
  grant_id: "44444444-4444-4444-8444-444444444444",
  request_id: "opaque-request-id",
  username: "reader_1",
  slug: "reader-parity",
  notification_id: `notif_${"a".repeat(26)}`,
  broadcast_id: `bcast_${"b".repeat(26)}`,
};

function clock() {
  let now = 0;
  let id = 0;
  const timers = new Map();
  const set = (callback, delay) => {
    const token = ++id;
    timers.set(token, { callback, at: now + delay });
    return token;
  };
  return {
    setTimeout: set,
    clearTimeout: (token) => timers.delete(token),
    advance(duration) {
      const until = now + duration;
      while (true) {
        const first = [...timers.entries()]
          .filter(([, timer]) => timer.at <= until)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!first) break;
        const [token, timer] = first;
        now = timer.at;
        timers.delete(token);
        timer.callback();
      }
      now = until;
    },
    signalTimeout(duration) {
      const controller = new AbortController();
      set(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), duration);
      return controller.signal;
    },
  };
}

function harness({ network, declared = operations } = {}) {
  const calls = [];
  const time = clock();
  const output = { exports: {} };
  runInNewContext(transportSource, {
    module: output,
    exports: output.exports,
    URL,
    URLSearchParams,
    Request,
    Response,
    Headers,
    DOMException,
    Uint8Array,
    ArrayBuffer,
    setTimeout: time.setTimeout,
    clearTimeout: time.clearTimeout,
    AbortSignal: {
      any: AbortSignal.any.bind(AbortSignal),
      timeout: time.signalTimeout,
    },
  });
  const transport = new output.exports.ExtensionReaderTransport(async function (url, init) {
    calls.push({ url, init, receiver: this });
    return network ? network(url, init) : Response.json({});
  }, declared);
  return { request: transport.request, calls, time };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function resource(operation) {
  return operation.path.replace(/\{([^}]+)\}/g, (_match, name) => {
    assert.ok(Object.hasOwn(params, name), `Missing fixture for parameter ${name}`);
    return encodeURIComponent(params[name]);
  });
}

test("generated extension operations declare policies and omit direct OAuth exchange operations", () => {
  assert.equal(operations.length, 84);
  assert.equal(
    new Set(operations.map((operation) => operation.operationId)).size,
    operations.length,
  );
  assert.equal(
    new Set(operations.map((operation) => `${operation.method} ${operation.path}`)).size,
    operations.length,
  );
  assert.ok(
    operations.some(
      (operation) =>
        operation.operationId === "user_auth_sso_logout" && operation.method === "POST",
    ),
  );
  for (const operation of operations) {
    assert.ok(operation.policy.extension);
    assert.ok(["include", "omit"].includes(operation.policy.extension.credentials));
    assert.ok([null, "public-read", "no-store"].includes(operation.policy.extension.cacheControl));
    assert.ok(
      operation.policy.extension.requestHeaders.every((name) =>
        ["Content-Type", "X-CSRF-Token", "If-None-Match"].includes(name),
      ),
    );
  }
  assert.ok(
    !operations.some((operation) =>
      [
        "user_auth_login",
        "user_auth_callback",
        "user_auth_backchannel_logout",
        "user_auth_sso_callback",
      ].includes(operation.operationId),
    ),
  );
});

test("calls an injected fetch function without binding it to the transport instance", async () => {
  const { request, calls } = harness();
  await request("/api/v1/feed");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].receiver, undefined);
});

for (const operation of operations) {
  test(`generated identity ${operation.operationId} uses its declared method, path and browser policy`, async () => {
    const { request, calls, time } = harness();
    const extension = operation.policy.extension;
    const body = '{"transport-fixture":true}';
    const response = await request(resource(operation) + "?cursor=next%2Bpage&tag=one&tag=two", {
      method: operation.method.toLowerCase(),
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf",
        "If-None-Match": '"fixture-etag"',
        Cookie: "private",
        Authorization: "private",
        Origin: "https://untrusted.example",
        "Cache-Control": "public",
      },
      credentials: extension.credentials === "omit" ? "include" : "omit",
      cache: "force-cache",
      redirect: "follow",
      referrerPolicy: "unsafe-url",
      ...(!["GET", "HEAD"].includes(operation.method) ? { body } : {}),
    });
    assert.equal(response.status, 200);
    assert.equal(calls.length, 1);
    const { url, init } = calls[0];
    const parsed = new URL(url);
    assert.equal(parsed.origin, origin);
    assert.equal(parsed.pathname, resource(operation));
    assert.equal(parsed.searchParams.get("cursor"), "next+page");
    assert.deepEqual(parsed.searchParams.getAll("tag"), ["one", "two"]);
    assert.equal(init.method, operation.method);
    assert.equal(init.credentials, extension.credentials);
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "error");
    assert.equal(init.referrerPolicy, "no-referrer");
    assert.equal(init.headers.get("accept"), "application/json");
    assert.equal(
      init.headers.get("cache-control"),
      extension.cacheControl === "public-read" ? "max-age=600" : extension.cacheControl,
    );
    for (const [name, value] of [
      ["Content-Type", "application/json"],
      ["X-CSRF-Token", "csrf"],
      ["If-None-Match", '"fixture-etag"'],
    ])
      assert.equal(init.headers.get(name), extension.requestHeaders.includes(name) ? value : null);
    for (const name of ["cookie", "authorization", "origin"])
      assert.equal(init.headers.get(name), null);
    assert.equal(init.body, ["GET", "HEAD"].includes(operation.method) ? undefined : body);
    time.advance(extension.timeoutMs - 1);
    assert.equal(init.signal.aborted, false);
    time.advance(1);
    assert.equal(init.signal.aborted, true);
    assert.equal(init.signal.reason.name, "TimeoutError");
  });
}

for (const [method, suffix] of [
  ["GET", "messages"],
  ["POST", "items"],
  ["GET", "read-all"],
  ["POST", "preferences"],
  ["GET", "stream/extra"],
  ["POST", `items/${params.notification_id}/read`],
  ["POST", "notifications/bad-id/read"],
  ["POST", `notifications/${params.broadcast_id}/read`],
  ["POST", `notifications/${params.notification_id}/unknown`],
]) {
  test(`denies undeclared or malformed inbox ${method} ${suffix} without forwarding`, async () => {
    const { request, calls } = harness();
    const original = new Request(`${origin}/api/v1/user/notifications/chimely/v1/inbox/${suffix}`, {
      method,
      ...(method === "POST" ? { body: new ReadableStream(), duplex: "half" } : {}),
    });
    assert.equal((await request(original)).status, 403);
    assert.equal(original.bodyUsed, false);
    assert.equal(calls.length, 0);
  });
}

for (const [path, method] of [
  ["/api/v1/user/unavailable-resource", "PATCH"],
  ["/api/v1/user/auth/me", "DELETE"],
  ["/api/v1/user/auth/config", "POST"],
  ["/api/v1/user/auth/logout", "GET"],
  ["/api/v1/user/auth/login?provider=github", "GET"],
  ["/api/v1/user/auth/callback?code=private", "GET"],
  ["/api/v1/admin/auth/me", "GET"],
  ["/api/v1/partner/accounts", "POST"],
  ["/api/v1/feed", "POST"],
  ["/api/v1/user/auth/me", "HEAD"],
  ["/api/v1/user/auth/me", "OPTIONS"],
  ["/api/v1/user/partner-placements/query", "DELETE"],
  ["/api/v1/partner-placements/query", "GET"],
  ["/api/v1/partner-tracking/events", "PUT"],
  ["/api/v1/users/reader_1/private", "GET"],
  ["/api/v1/topics/typescript/logo", "GET"],
  ["/api/v1/user/settings/profile/avatar/extra", "POST"],
]) {
  test(`denies undeclared ${method} ${path} before touching an original request body`, async () => {
    const { request, calls } = harness();
    const body = new ReadableStream();
    const original = new Request(origin + path, {
      method,
      ...(!["GET", "HEAD"].includes(method) ? { body, duplex: "half" } : {}),
    });
    const response = await request(original);
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { detail: "Unsupported reader request" });
    assert.equal(original.bodyUsed, false);
    assert.equal(calls.length, 0);
  });
}

for (const path of [
  "/api/v1/user/articles/not-a-uuid/open",
  "/api/v1/user/preferences/topics/not-a-uuid",
  "/api/v1/user/mcp/connections/%2Fgrant",
  "/api/v1/user/mcp/requests/%2Fauth%2Flogout",
  "/api/v1/user/mcp/requests/%25secret",
  "/api/v1/user/mcp/requests/%00secret",
  "/api/v1/user/mcp/requests/%5Csecret",
  "/api/v1/user/mcp/requests/%3Fsecret",
  "/api/v1/user/mcp/requests/%23secret",
  "/api/v1/user/mcp/requests/%E0%A4%A",
  "/api/v1/user/notifications/chimely/v1/inbox/preferences/%2Fprivate",
]) {
  test(`denies unsafe or invalid declared parameters: ${path}`, async () => {
    const { request, calls } = harness();
    const method = path.includes("/articles/")
      ? "POST"
      : path.includes("/preferences/topics/")
        ? "PUT"
        : path.includes("/mcp/connections/")
          ? "DELETE"
          : "GET";
    const response = await request(path, { method });
    assert.equal(response.status, 403);
    assert.equal(calls.length, 0);
  });
}

test("browser policy follows a renamed operation instead of the endpoint namespace or avatar filename", async () => {
  const template = operations[0];
  const declaration = {
    ...template,
    operationId: "renamed-public-read",
    path: "/api/v1/user/settings/profile/avatar",
    method: "GET",
    parameters: [],
    policy: {
      ...template.policy,
      extension: {
        credentials: "omit",
        cacheControl: "no-store",
        requestHeaders: [],
        timeoutMs: 15_000,
      },
    },
  };
  const { request, calls, time } = harness({ declared: [declaration] });
  await request(declaration.path, { headers: { "X-CSRF-Token": "private" } });
  assert.equal(calls[0].init.credentials, "omit");
  assert.equal(calls[0].init.headers.get("cache-control"), "no-store");
  assert.equal(calls[0].init.headers.get("x-csrf-token"), null);
  time.advance(15_000);
  assert.equal(calls[0].init.signal.aborted, true);
});

test("preserves concrete notification actions, conditional reads and CSRF-protected updates", async () => {
  const { request, calls } = harness();
  const path = `/api/v1/user/notifications/chimely/v1/inbox/notifications/${params.notification_id}/read`;
  await request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
    body: "{}",
  });
  assert.equal(calls[0].url, origin + path);
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.headers.get("x-csrf-token"), "csrf");
  await request("/api/v1/user/notifications/chimely/v1/inbox/items?limit=20", {
    headers: { "If-None-Match": '"inbox-etag"' },
  });
  assert.equal(calls[1].init.headers.get("if-none-match"), '"inbox-etag"');
});

test("accepts opaque MCP request and grant IDs without adding UUID constraints", async () => {
  const { request, calls } = harness();
  await request("/api/v1/user/mcp/requests/review-request");
  await request("/api/v1/user/mcp/connections/review-grant", {
    method: "DELETE",
    headers: { "X-CSRF-Token": "csrf" },
  });
  assert.equal(calls[0].url, `${origin}/api/v1/user/mcp/requests/review-request`);
  assert.equal(calls[1].url, `${origin}/api/v1/user/mcp/connections/review-grant`);
  assert.equal(calls[1].init.method, "DELETE");
});

test("rejects other origins and URL credentials before networking", async () => {
  const { request, calls } = harness();
  for (const url of [
    "https://evil.example/api/v1/feed",
    "http://devfeed.tech/api/v1/feed",
    "https://reader:private@devfeed.tech/api/v1/feed",
  ]) {
    await assert.rejects(request(url), /Unexpected reader API origin/);
  }
  assert.equal(calls.length, 0);
});

test("preserves Request method, bytes, multipart boundary and header overrides", async () => {
  const { request, calls } = harness();
  const bytes = new TextEncoder().encode(
    '--fixture\r\nContent-Disposition: form-data; name="file"\r\n\r\nimage\r\n--fixture--',
  );
  const original = new Request(origin + "/api/v1/user/settings/profile/avatar", {
    method: "POST",
    keepalive: true,
    headers: { "Content-Type": "multipart/form-data; boundary=fixture", "X-CSRF-Token": "csrf" },
    body: bytes,
  });
  await request(original);
  assert.deepEqual(new Uint8Array(calls[0].init.body), bytes);
  assert.equal(calls[0].init.headers.get("content-type"), "multipart/form-data; boundary=fixture");
  assert.equal(calls[0].init.headers.get("x-csrf-token"), "csrf");
  assert.equal(calls[0].init.keepalive, true);
  assert.equal(original.bodyUsed, false);
  const override = '{"view":"compact"}';
  await request(original, {
    method: "DELETE",
    headers: { "X-CSRF-Token": "replacement" },
    body: override,
    keepalive: false,
  });
  assert.equal(calls[1].init.method, "DELETE");
  assert.equal(calls[1].init.body, override);
  assert.equal(calls[1].init.headers.get("x-csrf-token"), "replacement");
  assert.equal(calls[1].init.headers.get("content-type"), null);
  assert.equal(calls[1].init.keepalive, false);
});

test("leaves FormData encoding to the browser and retains receipt keepalive without private headers", async () => {
  const { request, calls } = harness();
  const form = new FormData();
  form.append("file", new Blob(["image"], { type: "image/png" }), "avatar.png");
  await request("/api/v1/user/settings/profile/avatar", {
    method: "POST",
    headers: { "X-CSRF-Token": "csrf" },
    body: form,
  });
  assert.equal(calls[0].init.body, form);
  assert.equal(calls[0].init.headers.has("content-type"), false);
  await request("/api/v1/partner-tracking/events", {
    method: "POST",
    body: '{"receipt":"signed"}',
    keepalive: true,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": "private" },
  });
  assert.equal(calls[1].init.keepalive, true);
  assert.equal(calls[1].init.credentials, "omit");
  assert.equal(calls[1].init.headers.get("x-csrf-token"), null);
});

for (const [path, method, limit] of [
  ["/api/v1/user/preferences", "PUT", 1_000_000],
  ["/api/v1/user/settings/profile/avatar", "POST", 5 * 1024 * 1024 + 65536],
  ["/api/v1/partner-placements/query", "POST", 16384],
  ["/api/v1/partner-tracking/events", "POST", 2048],
]) {
  test(`bounds original Request bytes for ${method} ${path}`, async () => {
    const { request, calls } = harness();
    const exact = new Uint8Array(limit).fill(97);
    await request(new Request(origin + path, { method, body: exact }));
    assert.equal(calls.length, 1);
    assert.deepEqual(new Uint8Array(calls[0].init.body), exact);
    const tooLarge = new Request(origin + path, { method, body: new Uint8Array(limit + 1) });
    await assert.rejects(request(tooLarge), (error) => error.name === "RequestBodyTooLarge");
    assert.equal(calls.length, 1);
  });
}

for (const [path, deadline] of [
  ["/api/v1/user/preferences", 2000],
  ["/api/v1/user/settings/profile/avatar", 10_000],
]) {
  test(`bounds stalled original Request body collection at ${deadline}ms: ${path}`, async () => {
    const { request, calls, time } = harness();
    const original = new Request(origin + path, {
      method: path.endsWith("avatar") ? "POST" : "PUT",
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array([123]));
        },
      }),
      duplex: "half",
    });
    let settled = false;
    const result = request(original);
    const checked = assert.rejects(result, (error) => error.name === "RequestBodyTimeout");
    result.then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );
    await flush();
    time.advance(deadline - 1);
    await flush();
    assert.equal(settled, false);
    time.advance(1);
    await checked;
    assert.equal(calls.length, 0);
    await original.body.cancel();
  });
}

test("caller cancellation interrupts original Request collection before the body deadline", async () => {
  const { request, calls } = harness();
  const controller = new AbortController();
  const original = new Request(origin + "/api/v1/user/preferences", {
    method: "PUT",
    body: new ReadableStream(),
    duplex: "half",
  });
  const pending = request(original, { signal: controller.signal });
  const checked = assert.rejects(pending, (error) => error.name === "AbortError");
  await flush();
  controller.abort();
  await checked;
  assert.equal(calls.length, 0);
  await original.body.cancel();
});

test("an already canceled original request is rejected before networking", async () => {
  const { request, calls } = harness();
  const controller = new AbortController();
  controller.abort();
  const original = new Request(origin + "/api/v1/user/preferences", {
    method: "PUT",
    signal: controller.signal,
    body: "{}",
  });
  await assert.rejects(request(original), (error) => error.name === "AbortError");
  assert.equal(calls.length, 0);
});

test("starts the operation's overall timeout before collecting an original Request body", async () => {
  const operation = operations.find(
    (value) => value.path === "/api/v1/user/preferences" && value.method === "PUT",
  );
  const declared = [{ ...operation, policy: { ...operation.policy, bodyTimeoutMs: 60_000 } }];
  const { request, calls, time } = harness({ declared });
  const original = new Request(origin + operation.path, {
    method: "PUT",
    body: new ReadableStream(),
    duplex: "half",
  });
  const pending = request(original);
  const checked = assert.rejects(pending, (error) => error.name === "TimeoutError");
  await flush();
  time.advance(operation.policy.extension.timeoutMs - 1);
  await flush();
  assert.equal(calls.length, 0);
  time.advance(1);
  await checked;
  assert.equal(calls.length, 0);
  await original.body.cancel();
});

for (const [path, method, timeout] of [
  ["/api/v1/feed", "GET", 15_000],
  ["/api/v1/user/settings/profile/avatar", "POST", 45_000],
]) {
  test(`cancels in-flight networking at the declared ${timeout}ms timeout: ${path}`, async () => {
    const { request, calls, time } = harness({
      network: (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
        }),
    });
    const pending = request(path, { method, ...(method === "POST" ? { body: "image" } : {}) });
    const checked = assert.rejects(pending, (error) => error.name === "TimeoutError");
    await flush();
    assert.equal(calls.length, 1);
    time.advance(timeout - 1);
    assert.equal(calls[0].init.signal.aborted, false);
    time.advance(1);
    await checked;
  });
}

test("caller cancellation propagates to in-flight networking without replacing its reason", async () => {
  const controller = new AbortController();
  const { request, calls } = harness({
    network: (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
      }),
  });
  const reason = new DOMException("Account changed", "AbortError");
  const pending = request("/api/v1/user/feed", { signal: controller.signal });
  const checked = assert.rejects(pending, (error) => error === reason);
  await flush();
  controller.abort(reason);
  await checked;
  assert.equal(calls.length, 1);
});

test("HTTP errors and network failures remain visible to reader retry and authorization handling", async () => {
  const failing = harness({
    network: async () => Response.json({ detail: "Expired" }, { status: 401 }),
  });
  assert.equal((await failing.request("/api/v1/user/auth/me")).status, 401);
  const offline = harness({
    network: async () => {
      throw new Error("Offline");
    },
  });
  await assert.rejects(offline.request("/api/v1/feed"), /Offline/);
});

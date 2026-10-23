import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../../../..");
const readerCookies =
  "devfeed_user_session=session; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor";
const attributionCookies =
  "devfeed_user_x_click=click; __Host-devfeed_user_x_click=host-click; _twclid=pixel";
const incomingCookies = `${readerCookies}; ${attributionCookies}; devfeed_admin_session=private; devfeed_partner_session=private; unrelated=private`;
const requests = [];
const upstream = createServer(async (request, response) => {
  const record = {
    method: request.method,
    path: request.url,
    headers: request.headers,
    body: undefined,
  };
  requests.push(record);
  try {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    record.body = Buffer.concat(chunks);
    if (request.url.startsWith("/v1/user/auth/callback")) {
      response.writeHead(302, {
        Location: "https://reader.example/after-login",
        "Set-Cookie": ["session=rotated; Path=/; HttpOnly", "state=; Path=/; Max-Age=0"],
      });
      response.end();
      return;
    }
    if (request.url.startsWith("/v1/user/notifications/chimely/v1/inbox/stream")) {
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
        "X-Request-Id": "reader-gateway-fixture",
        "Retry-After": "2",
      });
      response.write("event: ping\ndata: first\n\n");
      const timer = setTimeout(() => response.end("event: ping\ndata: second\n\n"), 50);
      response.once("close", () => clearTimeout(timer));
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ items: [], received_bytes: record.body.byteLength }));
  } catch {
    response.destroy();
  }
});
await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const inheritedEnv = Object.fromEntries(
  Object.entries(process.env).filter(([name]) => !name.startsWith("DEVFEED_")),
);
const app = spawn(
  process.execPath,
  [
    path.join(root, "node_modules/next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: path.join(root, "apps/web"),
    env: {
      ...inheritedEnv,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_USER_API_URL: `http://127.0.0.1:${upstream.address().port}`,
      DEVFEED_PUBLIC_API_URL: `http://127.0.0.1:${upstream.address().port}`,
      DEVFEED_USER_EXTENSION_IDS: JSON.stringify(["a".repeat(32)]),
      DEVFEED_MCP_PUBLIC_URL: "https://mcp.example.com/mcp",
      DEVFEED_ANALYTICS_ENABLED: "false",
      DEVFEED_X_PIXEL_ENABLED: "true",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));

function headers(extra = {}) {
  return {
    Origin: origin,
    Cookie: incomingCookies,
    "X-CSRF-Token": "reader-csrf-fixture",
    "Content-Type": "application/json",
    ...extra,
  };
}

async function call(resource, method = "GET", body, extraHeaders = {}) {
  try {
    return await fetch(origin + resource, {
      method,
      headers: headers(extraHeaders),
      ...(body !== undefined ? { body } : {}),
      redirect: "manual",
      signal: AbortSignal.timeout(15_000),
    });
  } catch (cause) {
    const bytes = body === undefined ? 0 : Buffer.byteLength(body);
    throw new Error(`${method} ${resource} failed while sending ${bytes} bytes`, { cause });
  }
}

function allow(response) {
  return new Set(
    response.headers
      .get("allow")
      .split(",")
      .map((method) => method.trim()),
  );
}

async function checkRoutingBoundary() {
  for (const { resource, method, status, allowed } of [
    { resource: "/api/v1/admin/partner/accounts", method: "GET", status: 404 },
    { resource: "/api/v1/partner/accounts", method: "POST", status: 404 },
    { resource: "/api/v1/user/unavailable-resource", method: "PUT", status: 404 },
    { resource: "/api/v1/user/settings/profile/avatar/extra", method: "POST", status: 404 },
    { resource: "/api/v1/user/articles/not-a-uuid/open", method: "POST", status: 404 },
    { resource: "/api/v1/user/articles/open", method: "POST", status: 404 },
    { resource: "/api/v1/user/auth/me", method: "POST", status: 405 },
    { resource: "/api/v1/user/preferences", method: "POST", status: 405 },
    { resource: "/api/v1/user/auth/me", method: "HEAD", status: 405, allowed: ["GET"] },
    {
      resource: "/api/v1/user/preferences",
      method: "HEAD",
      status: 405,
      allowed: ["GET", "PUT"],
    },
    {
      resource: "/api/v1/user/preferences",
      method: "OPTIONS",
      status: 204,
      allowed: ["GET", "HEAD", "OPTIONS", "PUT"],
    },
  ]) {
    const before = requests.length;
    const response = await call(
      resource,
      method,
      ["GET", "HEAD", "OPTIONS"].includes(method) ? undefined : '{"boundary":true}',
    );
    assert.equal(response.status, status, `${method} ${resource}`);
    if (allowed) assert.deepEqual(allow(response), new Set(allowed));
    await response.arrayBuffer();
    assert.equal(requests.length, before, `${method} ${resource} must not reach the upstream`);
  }
  console.log(
    "Reader concrete route boundary: namespace isolation, parameters, methods, HEAD and native OPTIONS passed.",
  );
}

async function checkCookieAndForwardingPolicies() {
  for (const [resource, method, body] of [
    ["/api/v1/user/auth/me"],
    ["/api/v1/user/auth/callback?code=fixture-code"],
    ["/api/v1/user/feed"],
    ["/api/v1/user/settings/profile/avatar", "POST", "avatar fixture bytes"],
    ["/api/v1/user/auth/logout", "POST", "{}"],
  ]) {
    const response = await call(resource, method, body, { Authorization: "private" });
    assert.ok([200, 302].includes(response.status));
    await response.arrayBuffer();
    const request = requests.at(-1);
    assert.equal(request.headers.cookie, readerCookies, resource);
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers["x-csrf-token"], "reader-csrf-fixture");
    if (resource.includes("/auth/callback")) {
      assert.equal(response.headers.get("location"), "https://reader.example/after-login");
      assert.deepEqual(response.headers.getSetCookie(), [
        "session=rotated; Path=/; HttpOnly",
        "state=; Path=/; Max-Age=0",
      ]);
    }
  }
  const login = await call("/api/v1/user/auth/login?provider=github");
  assert.equal(login.status, 200);
  await login.arrayBuffer();
  assert.equal(requests.at(-1).headers.cookie, `${readerCookies}; ${attributionCookies}`);

  const before = requests.length;
  const rejected = await call("/api/v1/user/preferences", "PUT", "{}", {
    Origin: "https://untrusted.example",
  });
  assert.equal(rejected.status, 403);
  assert.equal(requests.length, before);
  const trusted = await call("/api/v1/user/preferences", "PUT", "{}", {
    Origin: `chrome-extension://${"a".repeat(32)}`,
  });
  assert.equal(trusted.status, 200);
  await trusted.arrayBuffer();
  assert.equal(requests.at(-1).headers.origin, `chrome-extension://${"a".repeat(32)}`);
  assert.equal(requests.at(-1).headers.cookie, readerCookies);
  const stream = await call(
    "/api/v1/user/notifications/chimely/v1/inbox/stream",
    "GET",
    undefined,
    {
      "Last-Event-ID": "notification-cursor",
      "If-None-Match": '"inbox-etag"',
    },
  );
  assert.equal(stream.status, 200);
  assert.equal(stream.headers.get("x-accel-buffering"), "no");
  assert.equal(stream.headers.get("retry-after"), "2");
  assert.equal(stream.headers.get("x-request-id"), "reader-gateway-fixture");
  assert.equal(requests.at(-1).headers["last-event-id"], "notification-cursor");
  assert.equal(requests.at(-1).headers["if-none-match"], '"inbox-etag"');
  assert.equal(await stream.text(), "event: ping\ndata: first\n\nevent: ping\ndata: second\n\n");
  console.log(
    "Reader forwarding: operation-scoped cookie privacy, static context, extension origin, callback cookies and inbox stream passed.",
  );
}

async function checkInboxOperations() {
  const inbox = "/api/v1/user/notifications/chimely/v1/inbox";
  const notificationId = `notif_${"a".repeat(26)}`;
  const broadcastId = `bcast_${"b".repeat(26)}`;
  const operations = [
    ...["items", "counts", "preferences", "stream"].map((path) => ({ method: "GET", path })),
    { method: "PUT", path: "preferences" },
    ...["read-all", "seen-all", "archive-all", "archive-read"].map((path) => ({
      method: "POST",
      path,
    })),
    ...[
      ["notifications", notificationId],
      ["broadcasts", broadcastId],
    ].flatMap(([kind, id]) =>
      ["read", "unread", "archive", "unarchive"].map((action) => ({
        method: "POST",
        path: `${kind}/${id}/${action}`,
      })),
    ),
  ];
  assert.equal(operations.length, 17);
  for (const { method, path } of operations) {
    const before = requests.length;
    const response = await call(
      `${inbox}/${path}`,
      method,
      method === "GET" ? undefined : method === "PUT" ? '{"preferences":[]}' : "{}",
      { Authorization: "private", "X-Chimely-Subscriber-Hash": "forged" },
    );
    assert.equal(response.status, 200, `${method} ${path}`);
    await response.arrayBuffer();
    assert.equal(requests.length, before + 1);
    const request = requests.at(-1);
    assert.equal(request.path, `/v1/user/notifications/chimely/v1/inbox/${path}`);
    assert.equal(request.method, method);
    assert.equal(request.headers.cookie, readerCookies);
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers["x-chimely-subscriber-hash"], undefined);
    assert.equal(request.headers["x-csrf-token"], "reader-csrf-fixture");
    assert.ok(response.headers.get("cache-control").includes("no-store"));
  }
  for (const { method, path, status, allowed } of [
    { method: "GET", path: "messages", status: 404 },
    { method: "GET", path: "unavailable", status: 404 },
    { method: "GET", path: "stream/extra", status: 404 },
    { method: "POST", path: `items/${notificationId}/read`, status: 404 },
    { method: "POST", path: "notifications/bad-id/read", status: 404 },
    { method: "POST", path: `notifications/${broadcastId}/read`, status: 404 },
    { method: "POST", path: `notifications/${notificationId}/unknown`, status: 404 },
    { method: "POST", path: "items", status: 405 },
    { method: "GET", path: `notifications/${notificationId}/read`, status: 405 },
    { method: "POST", path: "preferences", status: 405 },
    { method: "HEAD", path: "stream", status: 405, allowed: ["GET"] },
    { method: "HEAD", path: "preferences", status: 405, allowed: ["GET", "PUT"] },
    {
      method: "OPTIONS",
      path: `notifications/${notificationId}/read`,
      status: 204,
      allowed: ["OPTIONS", "POST"],
    },
  ]) {
    const before = requests.length;
    const response = await call(
      `${inbox}/${path}`,
      method,
      ["GET", "HEAD", "OPTIONS"].includes(method) ? undefined : "{}",
    );
    assert.equal(response.status, status, `${method} ${path}`);
    if (allowed) assert.deepEqual(allow(response), new Set(allowed));
    await response.arrayBuffer();
    assert.equal(requests.length, before, "Rejected inbox requests must not reach the upstream");
  }
  console.log(
    "All seventeen concrete inbox operations passed with reader cookies/CSRF; unknown routes, wrong methods and malformed IDs were rejected before forwarding.",
  );
}

async function checkExactByteLimits() {
  for (const { resource, method, limit } of [
    { resource: "/api/v1/user/preferences", method: "PUT", limit: 1_000_000 },
    {
      resource: "/api/v1/user/settings/profile/avatar",
      method: "POST",
      limit: 5 * 1024 * 1024 + 65536,
    },
    { resource: "/api/v1/user/settings/profile/avatar", method: "DELETE", limit: 1_000_000 },
  ]) {
    const exact = Buffer.alloc(limit, 97);
    const response = await call(resource, method, exact);
    assert.equal(response.status, 200, `${method} ${resource} accepts exactly ${limit} bytes`);
    assert.equal((await response.json()).received_bytes, limit);
    assert.deepEqual(requests.at(-1).body, exact);
    const before = requests.length;
    const rejected = await call(resource, method, Buffer.alloc(limit + 1, 97));
    assert.equal(rejected.status, 413, `${method} ${resource} rejects a declared overflow`);
    assert.deepEqual(await rejected.json(), { detail: "Request too large" });
    assert.equal(rejected.headers.get("connection"), "close");
    assert.equal(requests.length, before);
    const chunked = await rawRequest(resource, method, {
      body: Buffer.alloc(limit + 1, 97),
      headers: { "Transfer-Encoding": "chunked" },
    });
    assert.equal(chunked.status, 413, `${method} ${resource} rejects an actual overflow`);
    assert.deepEqual(JSON.parse(chunked.body.toString()), { detail: "Request too large" });
    assert.equal(chunked.headers.connection, "close");
    assert.equal(requests.length, before);
  }
  // Non-ASCII text still uses encoded byte limits, including streamed overflow.
  const avatar = "/api/v1/user/settings/profile/avatar";
  const avatarLimit = 5 * 1024 * 1024 + 65536;
  const unicode = "é".repeat(avatarLimit / 2);
  const accepted = await call(avatar, "POST", unicode);
  assert.equal(accepted.status, 200);
  assert.equal((await accepted.json()).received_bytes, avatarLimit);
  assert.deepEqual(requests.at(-1).body, Buffer.from(unicode));
  const unicodeBefore = requests.length;
  const declaredUnicode = await call(avatar, "POST", unicode + "x");
  assert.equal(declaredUnicode.status, 413);
  assert.deepEqual(await declaredUnicode.json(), { detail: "Request too large" });
  const chunkedUnicode = await rawRequest(avatar, "POST", { body: Buffer.from(unicode + "x") });
  assert.equal(chunkedUnicode.status, 413);
  assert.deepEqual(JSON.parse(chunkedUnicode.body.toString()), { detail: "Request too large" });
  assert.equal(requests.length, unicodeBefore);
  const resource = "/api/v1/user/partner-placements/query";
  const before = requests.length;
  const invalid = await call(resource, "POST", '{"surface":"feed","user_id":"private"}');
  assert.equal(invalid.status, 422);
  assert.equal(requests.length, before);
  const exact = Buffer.from('{"surface":"feed"}'.padEnd(16384, " "));
  const valid = await call(resource, "POST", exact);
  assert.equal(valid.status, 200);
  assert.equal((await valid.json()).received_bytes, 16384);
  assert.deepEqual(requests.at(-1).body, exact);
  assert.equal(requests.at(-1).headers.cookie, readerCookies);
  const overflowBefore = requests.length;
  const overflow = await call(resource, "POST", Buffer.concat([exact, Buffer.from(" ")]));
  assert.equal(overflow.status, 413);
  assert.deepEqual(await overflow.json(), { detail: "Request too large" });
  assert.equal(requests.length, overflowBefore);
  console.log(
    "Reader body limits: exact ordinary/avatar/placement byte boundaries, ASCII/UTF-8 declared and chunked overflow, dedicated placement validation passed.",
  );
}

async function rawRequest(resource, method, { body, headers: extraHeaders = {}, deadline } = {}) {
  let upload;
  let watchdog;
  try {
    return await new Promise((resolve, reject) => {
      let settled = false;
      const started = performance.now();
      const fail = (error) => {
        if (settled) return;
        settled = true;
        reject(error);
      };
      watchdog = setTimeout(
        () => fail(new Error(`${method} ${resource} did not respond within its watchdog`)),
        (deadline ?? 10_000) + 5000,
      );
      upload = httpRequest(
        origin + resource,
        {
          method,
          agent: false,
          headers: headers({ "Transfer-Encoding": "chunked", ...extraHeaders }),
        },
        (response) => {
          const chunks = [];
          response.on("data", (chunk) => chunks.push(chunk));
          response.once("error", fail);
          response.once("aborted", () => fail(new Error("Gateway response was interrupted")));
          response.once("end", () => {
            settled = true;
            resolve({
              status: response.statusCode,
              headers: response.headers,
              body: Buffer.concat(chunks),
              elapsedMs: performance.now() - started,
              uploadEnded: upload.writableEnded,
            });
          });
        },
      );
      upload.once("error", fail);
      if (body !== undefined) {
        upload.write(body);
        upload.end();
      } else {
        // This first chunk is deliberately followed by no end() call.
        upload.write('{"incomplete-private-fixture":');
      }
    });
  } finally {
    clearTimeout(watchdog);
    upload?.destroy();
  }
}

async function checkBodyDeadlines() {
  for (const { resource, method, deadline } of [
    { resource: "/api/v1/user/preferences", method: "PUT", deadline: 2000 },
    { resource: "/api/v1/user/partner-placements/query", method: "POST", deadline: 2000 },
    { resource: "/api/v1/user/settings/profile/avatar", method: "DELETE", deadline: 2000 },
    { resource: "/api/v1/user/settings/profile/avatar", method: "POST", deadline: 10_000 },
  ]) {
    const before = requests.length;
    const result = await rawRequest(resource, method, { deadline });
    assert.equal(result.status, 408, `${method} ${resource}`);
    assert.deepEqual(JSON.parse(result.body.toString()), { detail: "Incomplete request" });
    assert.equal(result.uploadEnded, false, "The response must precede upload completion");
    assert.ok(String(result.headers["cache-control"]).includes("no-store"));
    assert.equal(result.headers.connection, "close");
    assert.ok(
      result.elapsedMs >= deadline - 200 && result.elapsedMs < deadline + 4000,
      `${method} ${resource} responded after ${Math.round(result.elapsedMs)}ms`,
    );
    assert.equal(requests.length, before, "Incomplete bodies must never reach the upstream");
    console.log(
      `${method} ${resource}: incomplete upload returned408 in ${Math.round(result.elapsedMs)}ms.`,
    );
  }
}

try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (app.exitCode !== null) throw new Error("Built reader exited before becoming ready");
    try {
      const response = await call("/api/v1/user/auth/me");
      assert.equal(response.status, 200);
      await response.arrayBuffer();
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  assert.ok(ready, "Built reader became ready");
  await checkRoutingBoundary();
  await checkCookieAndForwardingPolicies();
  await checkInboxOperations();
  await checkExactByteLimits();
  await checkBodyDeadlines();
  console.log("Reader concrete gateway HTTP regressions passed against the production build.");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  if (app.exitCode === null) {
    app.kill("SIGTERM");
    await new Promise((resolve) => app.once("exit", resolve));
  }
  upstream.closeAllConnections();
  await new Promise((resolve) => upstream.close(resolve));
}

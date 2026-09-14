import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
async function bundled(path) {
  const compiled = await build({
    entryPoints: [new URL(path, import.meta.url).pathname],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    external: ["react"],
  });
  const module = { exports: {} };
  runInNewContext(compiled.outputFiles[0].text, {
    module,
    exports: module.exports,
    require,
    URL,
    Request,
    Headers,
    Response,
    AbortSignal,
  });
  return module.exports;
}
const { createReaderTransport } = await bundled("../src/transport.ts");
const { linkDestination } = await bundled("../src/navigation.tsx");

test("public reads use the website API, use the browser session, and propagate cancellation", async () => {
  const calls = [];
  const request = createReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ items: [], next_cursor: null });
  });
  const controller = new AbortController();
  const response = await request("/api/v1/feed?q=python%26rust&cursor=next%2Bpage", {
    signal: controller.signal,
    credentials: "include",
    headers: { Authorization: "private", Cookie: "secret" },
  });
  assert.equal(response.status, 200);
  const [url, init] = calls[0];
  assert.equal(new URL(url).origin, "https://devfeed.tech");
  assert.equal(new URL(url).searchParams.get("cursor"), "next+page");
  assert.equal(init.credentials, "include");
  assert.deepEqual([...init.headers.keys()], ["accept"]);
  controller.abort();
  assert.equal(init.signal.aborted, true);
});

test("account requests preserve JSON and CSRF while rejecting other APIs and credential headers", async () => {
  const calls = [];
  const request = createReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ user_id: "reader" });
  });
  assert.equal((await (await request("/api/v1/user/auth/me")).json()).user_id, "reader");
  await request("/api/v1/user/settings/feed", {
    method: "PUT",
    headers: {
      "X-CSRF-Token": "csrf",
      "Content-Type": "application/json",
      Authorization: "secret",
      Origin: "https://fake.test",
      Cookie: "secret",
    },
    body: '{"view":"compact"}',
  });
  const [, init] = calls[1];
  assert.equal(init.headers.get("x-csrf-token"), "csrf");
  assert.equal(init.headers.get("content-type"), "application/json");
  for (const name of ["authorization", "origin", "cookie"])
    assert.equal(init.headers.has(name), false);
  assert.equal(init.body, '{"view":"compact"}');
  assert.equal((await request("/api/v1/admin/auth/me")).status, 403);
  assert.equal((await request("/api/v1/user/auth/callback?code=unexpected")).status, 403);
  assert.equal(
    (await request(new Request("https://devfeed.tech/api/v1/feed", { method: "POST" }))).status,
    403,
  );
  await assert.rejects(request("https://other.test/api/v1/feed"), /Unexpected reader API origin/);
});

test("content tabs and search stay in the new tab; articles stay local and login uses the website", () => {
  assert.equal(linkDestination("/news?language=en"), "#/news?language=en");
  assert.equal(linkDestination("/search?q=rust"), "#/search?q=rust");
  assert.equal(linkDestination("/articles/rust-release"), "#/articles/rust-release");
  assert.equal(
    linkDestination("/api/v1/user/auth/login"),
    "https://devfeed.tech/api/v1/user/auth/login",
  );
  assert.equal(linkDestination("javascript:alert(1)"), "#");
});

test("network failures and HTTP errors remain visible to the reader retry controls", async () => {
  const unavailable = createReaderTransport(async () => Response.json({}, { status: 503 }));
  assert.equal((await unavailable("/api/v1/feed")).status, 503);
  const offline = createReaderTransport(async () => {
    throw new Error("Offline");
  });
  await assert.rejects(offline("/api/v1/feed"), /Offline/);
});

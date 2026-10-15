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
  const output = { exports: {} };
  runInNewContext(compiled.outputFiles[0].text, {
    module: output,
    exports: output.exports,
    require,
    URL,
    URLSearchParams,
    Request,
    Headers,
    Response,
    AbortSignal,
  });
  return output.exports;
}
const { ExtensionReaderTransport } = await bundled("../src/transport.ts");
const { linkDestination } = await bundled("../src/navigation.tsx");
const { resolveReaderRoute } = await bundled("../../web/src/lib/reader-routes.ts");
const { resolveReaderBackground } = await bundled("../src/reader-background.ts");
const { guestFeedFilters, searchParamsFromUrl, feedParams, feedHref } = await bundled(
  "../../web/src/lib/feed-query.ts",
);

test("extension guest home and Latest default to All without an implicit API content type", () => {
  for (const pathname of ["/", "/latest"]) {
    assert.equal(resolveReaderRoute(pathname).contentType, undefined);
    for (const query of ["", "content_type=", "content_type=invalid", "q=rust&cursor=next"]) {
      const filters = guestFeedFilters(searchParamsFromUrl(new URLSearchParams(query)));
      assert.equal(filters.content_type, "");
      assert.equal(feedParams(filters).has("content_type"), false);
    }
  }
});

test("extension Articles remains explicit and All clears the type without losing scope or sorting", () => {
  assert.equal(resolveReaderRoute("/articles").contentType, "article");
  const filters = guestFeedFilters({
    content_type: "article",
    topic: "rust",
    tag: "backend",
    q: "traits",
    sort: "oldest",
    cursor: "old",
  });
  assert.equal(feedParams(filters).get("content_type"), "article");
  const all = feedHref(filters, { content_type: "" });
  assert.equal(all, "/topics/rust?q=traits&sort=oldest&tag=backend");
  assert.equal(linkDestination(all), "#/topics/rust?q=traits&sort=oldest&tag=backend");
  const next = guestFeedFilters(
    searchParamsFromUrl(new URL(all, "https://devfeed.tech").searchParams),
  );
  assert.equal(next.content_type, "");
  assert.equal(feedParams(next).has("content_type"), false);
  assert.equal(next.cursor, "");
});

test("one route registry classifies every extension-owned page", () => {
  const route = (pathname) => JSON.stringify(resolveReaderRoute(pathname));
  assert.equal(route("/trending"), JSON.stringify({ type: "reader", page: "trending" }));
  assert.equal(route("/mcp/authorize"), JSON.stringify({ type: "local", page: "mcp-authorize" }));
  assert.equal(route("/mcp"), JSON.stringify({ type: "local", page: "mcp" }));
  assert.equal(route("/leaderboard"), JSON.stringify({ type: "local", page: "leaderboard" }));
  assert.equal(route("/dev-card"), JSON.stringify({ type: "local", page: "dev-card" }));
  assert.equal(linkDestination("/dev-card"), "#/dev-card");
  assert.equal(linkDestination("/leaderboard"), "#/leaderboard");
  assert.equal(
    route("/settings/topics"),
    JSON.stringify({
      type: "local",
      page: "settings",
      settings: "topics",
    }),
  );
  assert.equal(
    route("/sources/suggest"),
    JSON.stringify({
      type: "local",
      page: "source-suggestion",
    }),
  );
  assert.equal(
    route("/topics"),
    JSON.stringify({ type: "local", page: "catalog", catalog: "topics" }),
  );
  assert.equal(
    route("/topics/javascript/tutorials"),
    JSON.stringify({
      type: "reader",
      page: "feed",
      detail: { kind: "topics", slug: "javascript", contentType: "tutorial" },
    }),
  );
  assert.equal(
    route("/articles/release-notes"),
    JSON.stringify({
      type: "article",
      slug: "release-notes",
    }),
  );
  assert.equal(route("/users/reader_1"), JSON.stringify({ type: "profile", username: "reader_1" }));
  assert.equal(resolveReaderRoute("/users/a"), null);
  assert.equal(resolveReaderRoute("/legal/privacy"), null);
});

test("every catalog content suffix retains its filter for both catalog kinds", () => {
  const types = {
    articles: "article",
    news: "news",
    tutorials: "tutorial",
    releases: "release",
    comparisons: "comparison",
    opinions: "opinion",
  };
  for (const kind of ["topics", "sources"]) {
    assert.equal(resolveReaderRoute(`/${kind}/javascript`).detail.contentType, undefined);
    for (const [suffix, contentType] of Object.entries(types)) {
      const result = resolveReaderRoute(`/${kind}/javascript/${suffix}`);
      assert.equal(result.type, "reader");
      assert.equal(result.detail.kind, kind);
      assert.equal(result.detail.slug, "javascript");
      assert.equal(result.detail.contentType, contentType);
    }
    for (const suffix of ["invalid", "article", "tutorial", "news/extra", "NEWS", "-"]) {
      assert.equal(resolveReaderRoute(`/${kind}/javascript/${suffix}`), null);
    }
  }
});

test("unknown settings, routes, and malformed decoded slugs cannot become a feed", () => {
  for (const route of [
    "/missing",
    "/latest/extra",
    "/settings/missing",
    "/settings/profile/extra",
    "/mcp/unknown",
    "/articles/rust/extra",
    "/users/reader/extra",
    "/topics/rust/invalid",
    "/sources/devfeed/invalid",
  ]) {
    assert.equal(resolveReaderRoute(route), null, route);
  }
  for (const slug of ["%", "%ZZ", "%2F", "%5C", "%00"]) {
    for (const prefix of ["topics", "sources", "articles", "users"]) {
      const route = `/${prefix}/${slug}`;
      assert.equal(resolveReaderRoute(route), null, route);
    }
  }
  for (const slug of ["%252F", "bad%20slug", "c%2B%2B", "c%23", ".net"]) {
    for (const prefix of ["topics", "sources", "articles", "users"]) {
      const route = `/${prefix}/${slug}`;
      assert.equal(resolveReaderRoute(route), null, route);
    }
  }
});

test("catalog identifiers decode once while retaining their valid slug grammar", () => {
  for (const [encoded, slug] of [
    ["%72ust", "rust"],
    ["rust%2Drelease", "rust-release"],
  ]) {
    for (const kind of ["topics", "sources"]) {
      const result = resolveReaderRoute(`/${kind}/${encoded}/articles`);
      assert.equal(result.type, "reader");
      assert.equal(result.detail.kind, kind);
      assert.equal(result.detail.slug, slug);
      assert.equal(result.detail.contentType, "article");
      assert.equal(resolveReaderRoute(`/${kind}/%2572ust/articles`), null);
    }
  }
});

test("article previews retain reader background identity and query filters", () => {
  for (const [route, page] of [
    ["/", "personal"],
    ["/latest?cursor=next&content_type=news", "feed"],
    ["/search?q=rust", "search"],
    ["/read-later?cursor=next", "bookmarks"],
    ["/trending", "trending"],
  ]) {
    const background = resolveReaderBackground(route);
    assert.equal(background.route, route);
    assert.equal(background.match.type, "reader");
    assert.equal(background.match.page, page);
  }
  const route = "/topics/javascript/tutorials?cursor=next&tag=react";
  const background = resolveReaderBackground(route);
  assert.equal(background.route, route);
  assert.equal(background.match.detail.kind, "topics");
  assert.equal(background.match.detail.slug, "javascript");
  assert.equal(background.match.detail.contentType, "tutorial");
});

test("only an absent article background receives the direct-entry Latest page", () => {
  for (const value of [undefined, null]) {
    const background = resolveReaderBackground(value);
    assert.equal(background.route, "/latest");
    assert.equal(background.match.page, "feed");
  }
  for (const value of [
    "",
    "/unknown",
    "/topics/rust/unknown",
    "/sources/devfeed/unknown",
    "/settings/profile",
    "/topics",
    "/users/reader",
    "/articles/rust",
    "https://outside.test/latest",
    "//outside.test/latest",
    "http://[",
    "javascript:alert(1)",
    "/latest#unexpected",
    42,
    {},
    [],
  ]) {
    assert.equal(resolveReaderBackground(value), null, String(value));
  }
});

test("public reads use the website API, use the browser session, and propagate cancellation", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ items: [], next_cursor: null });
  }).request;
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
  assert.deepEqual([...init.headers.keys()], ["accept", "cache-control"]);
  assert.equal(init.headers.get("cache-control"), "max-age=600");
  controller.abort();
  assert.equal(init.signal.aborted, true);
});

test("account requests preserve JSON and CSRF while rejecting other APIs and credential headers", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ user_id: "reader" });
  }).request;
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

test("avatar uploads preserve multipart bodies and CSRF for both reader extensions", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ avatar_url: null, avatar_variants: [] });
  }).request;
  const body = new FormData();
  body.append("file", new Blob(["image"], { type: "image/png" }), "avatar.png");
  await request("/api/v1/user/settings/profile/avatar", {
    method: "POST",
    headers: { "X-CSRF-Token": "csrf" },
    body,
  });
  assert.equal(calls[0][0], "https://devfeed.tech/api/v1/user/settings/profile/avatar");
  assert.equal(calls[0][1].body, body);
  assert.equal(calls[0][1].credentials, "include");
  assert.equal(calls[0][1].headers.get("x-csrf-token"), "csrf");
  assert.equal(calls[0][1].headers.has("content-type"), false);
  await request("/api/v1/user/settings/profile/avatar", {
    method: "DELETE",
    headers: { "X-CSRF-Token": "csrf" },
  });
  assert.equal(calls[1][1].method, "DELETE");
  assert.equal(calls[1][1].headers.get("x-csrf-token"), "csrf");
});

test("content tabs and search stay in the new tab; articles stay local and login uses the website", () => {
  assert.equal(linkDestination("/news?language=en"), "#/news?language=en");
  assert.equal(linkDestination("/search?q=rust"), "#/search?q=rust");
  assert.equal(linkDestination("/articles/rust-release"), "#/articles/rust-release");
  assert.equal(
    linkDestination("/login?return_to=%2Fextension%2Flogin-complete"),
    "https://devfeed.tech/login?return_to=%2Fextension%2Flogin-complete",
  );
  assert.equal(linkDestination("javascript:alert(1)"), "#");
});

test("network failures and HTTP errors remain visible to the reader retry controls", async () => {
  const unavailable = new ExtensionReaderTransport(async () => Response.json({}, { status: 503 }))
    .request;
  assert.equal((await unavailable("/api/v1/feed")).status, 503);
  const offline = new ExtensionReaderTransport(async () => {
    throw new Error("Offline");
  }).request;
  await assert.rejects(offline("/api/v1/feed"), /Offline/);
});

test("search click analytics remains an allowed public write", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return new Response(null, { status: 204 });
  }).request;
  const response = await request("/api/v1/search/analytics/click", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "secret" },
    body: JSON.stringify({ query: "python", result_id: "article-1" }),
  });
  assert.equal(response.status, 204);
  const [url, init] = calls[0];
  assert.equal(url, "https://devfeed.tech/api/v1/search/analytics/click");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.get("content-type"), "application/json");
  assert.equal(init.headers.has("authorization"), false);
  assert.equal((await request("/api/v1/search/analytics/click", { method: "PUT" })).status, 403);
  assert.equal(
    (await request("/api/v1/search/analytics/impression", { method: "POST" })).status,
    403,
  );
});

for (const detail of [
  "Feed validation failed: The feed must contain at least 3 distinct usable entries.",
  "Feed validation failed: The feed must contain at least 1 entry dated within the last 3 months.",
]) {
  test(`Chrome/Edge source submissions preserve admission rejection: ${detail}`, async () => {
    const request = new ExtensionReaderTransport(async (url, init) => {
      assert.equal(url, "https://devfeed.tech/api/v1/user/sources/suggestions");
      assert.equal(init.method, "POST");
      assert.equal(init.headers.get("x-csrf-token"), "csrf");
      return Response.json({ detail }, { status: 422 });
    }).request;
    const response = await request("/api/v1/user/sources/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: JSON.stringify({ feed_url: "https://example.com/rss", source_type: "publisher" }),
    });
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { detail });
  });
}

test("catalog detail reads use one bounded public GET and cannot access other paths", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (url, init) => {
    calls.push({ url, init });
    return Response.json({ id: "one", slug: "late-topic" });
  }).request;
  for (const kind of ["topics", "sources"]) {
    assert.equal((await request(`/api/v1/${kind}/late-topic`)).status, 200);
    assert.equal(calls.at(-1).url, `https://devfeed.tech/api/v1/${kind}/late-topic`);
    assert.equal((await request(`/api/v1/${kind}/late-topic`, { method: "PUT" })).status, 403);
    assert.equal((await request(`/api/v1/${kind}/late-topic/private`)).status, 403);
  }
  assert.equal(calls.length, 2);
});

test("catalog logo export is no longer exposed through the reader transport", async () => {
  const calls = [];
  const transport = new ExtensionReaderTransport(async (url, options) => {
    calls.push({ url, options });
    return Response.json({ image: "data:image/webp;base64,AAAA" });
  }).request;
  assert.equal((await transport("/api/v1/topics/rancher/logo")).status, 403);
  assert.equal((await transport("/api/v1/topics/rancher/logo", { method: "POST" })).status, 403);
  assert.equal((await transport("/api/v1/topics/rancher/logo/extra")).status, 403);
  assert.equal(calls.length, 0);
});

test("MCP configuration reads bypass feed caching and reject mutations", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ url: "https://mcp.ingress.test/devfeed/mcp" });
  }).request;
  const response = await request("/api/v1/mcp/config");
  assert.equal((await response.json()).url, "https://mcp.ingress.test/devfeed/mcp");
  assert.equal(calls[0][0], "https://devfeed.tech/api/v1/mcp/config");
  assert.equal(calls[0][1].headers.get("Cache-Control"), "no-store");
  assert.equal(calls[0][1].cache, "no-store");
  assert.equal((await request("/api/v1/mcp/config", { method: "POST" })).status, 403);
  assert.equal(calls.length, 1);
});

test("public leaderboard reads bypass caching and reject mutations", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ longest_streak: [], reading_days: [] });
  }).request;
  assert.equal((await request("/api/v1/leaderboard")).status, 200);
  assert.equal(calls[0][0], "https://devfeed.tech/api/v1/leaderboard");
  assert.equal(calls[0][1].credentials, "omit");
  assert.equal(calls[0][1].headers.get("Cache-Control"), "no-store");
  assert.equal(calls[0][1].cache, "no-store");
  assert.equal((await request("/api/v1/leaderboard", { method: "POST" })).status, 403);
  assert.equal((await request("/api/v1/leaderboard/private")).status, 403);
  assert.equal(calls.length, 1);
});

test("partner impression and click events omit credentials and cannot proxy partner administration", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return new Response(null, { status: 204 });
  }).request;
  for (const kind of ["impression", "click"]) {
    const body = JSON.stringify({ kind, receipt: "signed-receipt" });
    assert.equal(
      (
        await request("/api/v1/partner-tracking/events", {
          method: "POST",
          body,
          keepalive: true,
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": "private",
            Authorization: "private",
          },
        })
      ).status,
      204,
    );
    const [url, init] = calls.at(-1);
    assert.equal(url, "https://devfeed.tech/api/v1/partner-tracking/events");
    assert.equal(init.credentials, "omit");
    assert.equal(init.headers.get("cache-control"), "no-store");
    assert.equal(init.headers.get("x-csrf-token"), null);
    assert.equal(init.headers.get("authorization"), null);
    assert.equal(init.body, body);
    assert.equal(init.keepalive, true);
  }
  assert.equal((await request("/api/v1/partner/accounts", { method: "POST" })).status, 403);
  assert.equal((await request("/api/v1/partner-tracking/events")).status, 403);
  assert.equal(calls.length, 2);
});

test("contextual placements are anonymous writes while personalized placement queries retain session CSRF", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ items: [] });
  }).request;
  const body = JSON.stringify({ surface: "feed", exclude_product_ids: [] });
  const headers = {
    "Content-Type": "application/json",
    "X-CSRF-Token": "csrf",
    Authorization: "private",
  };
  await request("/api/v1/partner-placements/query", { method: "POST", headers, body });
  assert.equal(calls[0][1].credentials, "omit");
  assert.equal(calls[0][1].headers.get("x-csrf-token"), null);
  assert.equal(calls[0][1].headers.get("authorization"), null);
  assert.equal(calls[0][1].headers.get("cache-control"), "no-store");
  assert.equal(calls[0][1].body, body);
  await request("/api/v1/user/partner-placements/query", { method: "POST", headers, body });
  assert.equal(calls[1][1].credentials, "include");
  assert.equal(calls[1][1].headers.get("x-csrf-token"), "csrf");
  assert.equal(calls[1][1].headers.get("authorization"), null);
  assert.equal((await request("/api/v1/partner-placements/query")).status, 403);
  assert.equal(
    (await request("/api/v1/partner-placements/query", { method: "DELETE" })).status,
    403,
  );
  assert.equal(
    (await request("/api/v1/partner-placements/private", { method: "POST" })).status,
    403,
  );
  assert.equal(calls.length, 2);
});

test("public profile visibility checks omit credentials and request fresh responses", async () => {
  const calls = [];
  const request = new ExtensionReaderTransport(async (...args) => {
    calls.push(args);
    return Response.json({ profile: null });
  }).request;
  await request("/api/v1/users/reader?include_activity=false", { credentials: "same-origin" });
  assert.equal(calls[0][1].credentials, "omit");
  assert.equal(calls[0][1].cache, "no-store");
  assert.equal(calls[0][1].headers.get("Cache-Control"), "no-store");
});

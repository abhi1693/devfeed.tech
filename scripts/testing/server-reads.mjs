import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const apiPath = (path) => path.replace(/^\/api(?=\/v1\/)/, "");
const publication = "2030-02-03T04:05:06.000Z";

/** Capture actual server/extension transport requests, including requests without sessions. */
export function serverReadFixture() {
  return {
    requests: [],
    unavailable: false,
    record(path, headers, origin) {
      this.requests.push({
        path: apiPath(new URL(path, "https://fixture").pathname),
        headers,
        origin,
      });
    },
    handles(url) {
      return ["/v1/sitemaps", "/v1/sitemaps/articles/1", "/v1/sitemaps/topics/1"].includes(
        url.pathname,
      );
    },
    respond(url) {
      if (this.unavailable) return { status: 503, json: { detail: "Fixture outage" } };
      if (url.pathname === "/v1/sitemaps")
        return {
          status: 200,
          json: {
            generation: "a".repeat(32),
            parts: [
              { kind: "articles", page: 1, modified_at: Date.parse(publication) / 1000 },
              { kind: "topics", page: 1 },
            ],
            latest_publication: {
              articles: publication,
              "feed:news": "2030-02-02T01:02:03Z",
              topics: "2030-02-01T00:00:00Z",
              sources: "2030-01-31T00:00:00Z",
            },
          },
        };
      const path =
        url.pathname === "/v1/sitemaps/articles/1" ? "/articles/fixture" : "/topics/typescript";
      return { status: 200, json: { paths: [path], entries: [{ path, lastmod: publication }] } };
    },
  };
}

function matching(fixture, path) {
  return fixture.requests.filter((request) => request.path === path);
}

function assertPublic(requests, origin) {
  assert.ok(requests.length, "Public feed reaches the public upstream");
  for (const request of requests) {
    assert.equal(request.origin, origin);
    assert.equal(request.headers["cache-control"], "max-age=600");
    assert.equal(request.headers.cookie, undefined, "Public reads cannot forward reader cookies");
    assert.equal(request.headers.authorization, undefined);
  }
}

function assertPrivate(requests, origin, cookie, extension = false) {
  assert.ok(requests.length, "Private operation reaches the user upstream");
  for (const request of requests) {
    assert.equal(request.origin, origin);
    if (extension)
      assert.ok(
        [undefined, "no-cache", "no-store"].includes(request.headers["cache-control"]),
        "Private extension operations cannot opt into the public API cache",
      );
    else assert.equal(request.headers["cache-control"], "no-store");
    assert.equal(request.headers.cookie, cookie);
    assert.equal(request.headers.authorization, undefined);
  }
}

/** Public/private cache policy follows operation ownership, even with no session cookie. */
export async function checkServerReads(page, destination, fixture, output, options) {
  await mkdir(output, { recursive: true });
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  fixture.requests.length = 0;
  await page.goto(destination("/latest?q=server-read-policy"));
  await page.locator(".article-card").first().waitFor();
  assertPublic(matching(fixture, "/v1/feed"), options.publicOrigin);

  fixture.requests.length = 0;
  await page.goto(destination("/trending"));
  await page.locator(".article-card").first().waitFor();
  assertPrivate(
    matching(fixture, "/v1/user/trending"),
    options.userOrigin,
    undefined,
    options.extension,
  );
  await page.screenshot({ path: `${output}/trending.png`, animations: "disabled" });
  if (options.extension) {
    await page.context().addCookies([
      {
        name: "devfeed_user_session",
        value: "private-reader-session",
        url: options.publicOrigin,
        httpOnly: true,
        secure: true,
      },
    ]);
    fixture.requests.length = 0;
    await page.goto(destination("/latest?q=credential-isolation"));
    await page.locator(".article-card").first().waitFor();
    assertPublic(matching(fixture, "/v1/feed"), options.publicOrigin);
    return;
  }

  await page.context().addCookies([
    { name: "devfeed_user_session", value: "valid", url: destination("/") },
    { name: "devfeed_admin_session", value: "must-not-forward", url: destination("/") },
    { name: "unrelated_cookie", value: "must-not-forward", url: destination("/") },
  ]);
  fixture.requests.length = 0;
  // Inspect server rendering before any browser/client transport can mask its headers.
  const latest = await page.context().request.get(destination("/latest?q=authenticated-read"));
  assert.equal(latest.status(), 200);
  assertPublic(matching(fixture, "/v1/feed"), options.publicOrigin);
  assertPrivate(
    matching(fixture, "/v1/user/settings/feed"),
    options.userOrigin,
    "devfeed_user_session=valid",
  );
  fixture.requests.length = 0;
  const home = await page.context().request.get(destination("/"));
  assert.equal(home.status(), 200);
  assertPrivate(
    matching(fixture, "/v1/user/auth/me"),
    options.userOrigin,
    "devfeed_user_session=valid",
  );
}

function xmlEntries(document, kind) {
  return [...document.matchAll(new RegExp(`<${kind}>\\s*([\\s\\S]*?)\\s*</${kind}>`, "g"))].map(
    ([, body]) =>
      Object.fromEntries(
        [...body.matchAll(/<(loc|lastmod|changefreq|priority)>(.*?)<\/\1>/g)].map(
          ([, key, value]) => [key, value],
        ),
      ),
  );
}

/** Published sitemap responses retain page hints, dates, conditional reads and outage policy. */
export async function checkSitemapPolicy(request, origin, fixture) {
  const pages = await request.get(`${origin}/sitemap-pages.xml`);
  assert.equal(pages.status(), 200);
  assert.equal(pages.headers()["cache-control"], "public, max-age=300, s-maxage=300");
  const entries = xmlEntries(await pages.text(), "url");
  assert.equal(entries.length, 11);
  assert.ok(entries.every((entry) => new URL(entry.loc).origin === origin));
  const entry = (path) => entries.find((item) => item.loc === origin + path);
  assert.deepEqual(entry("/latest"), {
    loc: origin + "/latest",
    lastmod: publication,
    changefreq: "hourly",
    priority: "1.0",
  });
  assert.equal(entry("/news").lastmod, "2030-02-02T01:02:03.000Z");
  assert.equal(entry("/news").changefreq, "hourly");
  assert.equal(entry("/news").priority, "0.7");
  assert.equal(entry("/topics").changefreq, "daily");
  assert.equal(entry("/topics").priority, "0.7");
  assert.equal(entry("/sources").changefreq, "daily");
  assert.deepEqual(entry("/legal/privacy"), {
    loc: origin + "/legal/privacy",
    lastmod: "2026-09-14T00:00:00.000Z",
    changefreq: "monthly",
    priority: "0.2",
  });
  assert.equal(entry("/legal/terms").lastmod, "2026-09-13T00:00:00.000Z");
  const unchanged = await request.get(`${origin}/sitemap-pages.xml`, {
    headers: { "If-None-Match": pages.headers().etag },
  });
  assert.equal(unchanged.status(), 304);
  assert.equal(await unchanged.text(), "");
  assert.equal(unchanged.headers().etag, pages.headers().etag);

  const index = await request.get(`${origin}/sitemap.xml`);
  assert.equal(index.status(), 200);
  assert.equal(index.headers()["cache-control"], "public, max-age=60, s-maxage=60");
  assert.deepEqual(xmlEntries(await index.text(), "sitemap"), [
    { loc: origin + "/sitemap-pages.xml", lastmod: publication },
    { loc: origin + "/sitemap-articles-1.xml", lastmod: publication },
    { loc: origin + "/sitemap-topics-1.xml" },
  ]);
  const unchangedIndex = await request.get(`${origin}/sitemap.xml`, {
    headers: { "If-None-Match": index.headers().etag },
  });
  assert.equal(unchangedIndex.status(), 304);
  for (const [kind, path, frequency, priority] of [
    ["articles", "/articles/fixture", "monthly", "0.8"],
    ["topics", "/topics/typescript", "daily", "0.6"],
  ]) {
    const part = await request.get(`${origin}/sitemap-${kind}-1.xml`);
    assert.equal(part.status(), 200);
    assert.deepEqual(xmlEntries(await part.text(), "url"), [
      { loc: origin + path, lastmod: publication, changefreq: frequency, priority },
    ]);
  }
  fixture.unavailable = true;
  try {
    for (const path of ["/sitemap.xml", "/sitemap-pages.xml", "/sitemap-articles-1.xml"]) {
      const unavailable = await request.get(origin + path, {
        headers: { "If-None-Match": pages.headers().etag },
      });
      assert.equal(unavailable.status(), 503);
      assert.equal(unavailable.headers()["cache-control"], "no-store");
      assert.equal(unavailable.headers()["retry-after"], "5");
      assert.equal(unavailable.headers().etag, undefined);
    }
  } finally {
    fixture.unavailable = false;
  }
  const inventoryRequests = fixture.requests.filter((request) =>
    ["/v1/sitemaps", "/v1/sitemaps/articles/1", "/v1/sitemaps/topics/1"].includes(request.path),
  );
  assert.ok(inventoryRequests.length);
  for (const request of inventoryRequests) {
    assert.equal(request.headers["cache-control"], "no-store");
    assert.equal(
      request.headers.cookie,
      undefined,
      "Sitemap inventory never forwards reader cookies",
    );
    assert.equal(request.headers.authorization, undefined);
  }
}

/** Legal pages publish the same document identity used by navigation and the sitemap. */
export async function checkLegalMetadata(page, origin) {
  for (const [path, title, updated, date] of [
    ["/legal/privacy", "Privacy Policy", "2026-09-14", "September 14, 2026"],
    ["/legal/terms", "Terms of Service", "2026-09-13", "September 13, 2026"],
  ]) {
    await page.goto(origin + path);
    await page.getByRole("heading", { name: title, exact: true }).waitFor();
    assert.equal(await page.locator(".legal-updated time").getAttribute("datetime"), updated);
    assert.equal(await page.locator(".legal-updated time").textContent(), date);
    assert.equal(await page.locator('link[rel="canonical"]').getAttribute("href"), origin + path);
    const navigation = page.getByRole("navigation", { name: "Legal documents", exact: true });
    assert.deepEqual(await navigation.getByRole("link").allTextContents(), [
      "Terms of Service",
      "Privacy Policy",
    ]);
    assert.deepEqual(
      await navigation
        .getByRole("link")
        .evaluateAll((links) => links.map((link) => link.getAttribute("href"))),
      ["/legal/terms", "/legal/privacy"],
    );
    assert.equal(await navigation.locator('[aria-current="page"]').textContent(), title);
    const structured = await page.locator('script[type="application/ld+json"]').allTextContents();
    const document = structured
      .map((text) => JSON.parse(text))
      .find((value) => value["@type"] === "WebPage");
    assert.equal(document.name, title);
    assert.equal(document.url, origin + path);
    assert.equal(document.dateModified, updated);
  }
}

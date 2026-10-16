import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { blockedFeed } from "../../apps/web/tests/browser/article-first.mjs";

/** Hold results until the public shell and its loading announcement are visible. */
export async function checkGuestDocument(page, destination, setGate, output) {
  await mkdir(dirname(output), { recursive: true });
  await page.context().addInitScript(() => {
    if (location.protocol !== "about:") localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  await page.bringToFront();
  for (const width of [1350, 412]) {
    await page.setViewportSize({ width, height: 823 });
    await page.goto("about:blank");
    const gate = blockedFeed();
    setGate(gate);
    try {
      const response = await page.goto(destination(`/?q=guest-document-${width}`), {
        waitUntil: "commit",
        timeout: 10000,
      });
      await gate.firstRequest;
      await page
        .getByRole("status", { name: "Loading articles…", exact: true })
        .waitFor({ timeout: 5000 });
      await page.getByRole("link", { name: "DevFeed home", exact: true }).waitFor();
      assert.equal(await page.locator(".article-card").count(), 0);
      if (new URL(page.url()).protocol !== "chrome-extension:") {
        assert.equal(response.status(), 200);
        assert.match(response.headers()["cache-control"], /private/);
        const nonce = response.headers()["content-security-policy"].match(/'nonce-([^']+)'/)[1];
        assert.equal(
          await page
            .locator("script[nonce]")
            .first()
            .evaluate((node) => node.nonce),
          nonce,
        );
        assert.ok(
          await page.locator('head link[rel="canonical"]').count(),
          "Canonical remains in the initial head",
        );
      }
      await page.screenshot({ path: `${output}-${width}-loading.png`, animations: "disabled" });
      gate.release();
      await page.locator(".article-card").first().waitFor();
      await page
        .getByRole("status", { name: "Loading articles…", exact: true })
        .waitFor({ state: "hidden" });
      await page.screenshot({ path: `${output}-${width}-ready.png`, animations: "disabled" });
    } finally {
      gate.release();
      setGate(undefined);
    }
  }
}

/** Backend responses can change between documents, including concurrent reader preferences. */
export function renderReadFixture(article, topic) {
  return {
    active: false,
    visible: true,
    empty: false,
    requests: [],
    handles(url) {
      return (
        this.active &&
        (url.pathname === "/v1/user/settings/feed" ||
          url.pathname === "/v1/topics/render-latency" ||
          (["/v1/feed", "/v1/feed/options"].includes(url.pathname) &&
            url.searchParams.get("topic") === "render-latency"))
      );
    },
    respond(url, headers) {
      this.requests.push({
        path: url.pathname,
        query: url.search,
        cookie: headers.cookie,
        cacheControl: headers["cache-control"],
      });
      if (url.pathname === "/v1/topics/render-latency")
        return this.visible
          ? { status: 200, json: { ...topic, slug: "render-latency" } }
          : { status: 404, json: { detail: "Not found" } };
      if (url.pathname === "/v1/user/settings/feed")
        return {
          status: 200,
          json: {
            content_types: ["tutorial"],
            languages: headers.cookie?.includes("beta") ? ["fr"] : ["en"],
          },
        };
      if (url.pathname === "/v1/feed/options")
        return {
          status: 200,
          json: { sources: [], content_types: ["tutorial"], languages: ["en", "fr"] },
        };
      const title =
        url.searchParams.get("languages") === "fr"
          ? "French render snapshot"
          : "English render snapshot";
      return {
        status: 200,
        json: { items: this.empty ? [] : [{ ...article, title }], next_cursor: null },
      };
    },
  };
}

export async function checkRenderReads(request, origin, fixture) {
  fixture.active = true;
  const href = origin + "/topics/render-latency";
  try {
    const responses = await Promise.all(
      ["alpha", "beta"].map((reader) =>
        request.get(href, { headers: { Cookie: `devfeed_user_session=${reader}` } }),
      ),
    );
    const bodies = await Promise.all(responses.map((response) => response.text()));
    for (let i = 0; i < 2; i++) {
      assert.equal(responses[i].status(), 200);
      assert.match(responses[i].headers()["cache-control"], /private/);
      assert.ok(bodies[i].includes(i ? "French render snapshot" : "English render snapshot"));
      assert.ok(!bodies[i].includes(i ? "English render snapshot" : "French render snapshot"));
    }
    const nonces = responses.map(
      (response) => response.headers()["content-security-policy"].match(/'nonce-([^']+)'/)[1],
    );
    assert.notEqual(...nonces, "Concurrent documents have different CSP nonces");
    const feeds = fixture.requests.filter(({ path }) => path === "/v1/feed");
    assert.equal(feeds.length, 2, "Metadata and content share one feed read for each document");
    assert.deepEqual(feeds.map(({ query }) => new URLSearchParams(query).get("languages")).sort(), [
      "en",
      "fr",
    ]);
    assert.equal(
      fixture.requests.filter(({ path }) => path === "/v1/user/settings/feed").length,
      2,
    );
    for (const read of fixture.requests.filter(({ path }) => !path.startsWith("/v1/user/"))) {
      assert.equal(read.cookie, undefined);
      assert.equal(read.cacheControl, "max-age=600");
    }
    fixture.empty = true;
    const empty = await request.get(href);
    assert.equal(empty.status(), 200);
    const html = await empty.text();
    assert.ok(
      !html.includes("English render snapshot") && !html.includes("French render snapshot"),
    );
    assert.match(html, /<meta name="robots" content="noindex, follow"/);
    fixture.visible = false;
    assert.equal(
      (await request.get(href)).status(),
      404,
      "A later visibility change bypasses the prior render cache",
    );
    fixture.visible = true;
    fixture.empty = false;
    assert.ok(
      (await (await request.get(href)).text()).includes("English render snapshot"),
      "A subsequent request sees republished content",
    );
  } finally {
    fixture.active = false;
  }
}

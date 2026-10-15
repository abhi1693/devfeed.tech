import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { feedModeFixture } from "./feed-mode.mjs";
import { dailyFixture } from "./must-reads.mjs";

const apiPath = (url) => url.pathname.replace(/^\/api(?=\/v1\/)/, "");
const visitsKey = "devfeed:signup-nudge-articles";

/** Actual article data, session state, and claim evidence shared by all reader bundles. */
export function readerContextFixture(article) {
  const feed = feedModeFixture(article);
  const articles = feed.articles.slice(0, 4).map((item, index) => ({
    ...item,
    slug: `context-article-${index + 1}`,
    title: `Reader context article ${index + 1}`,
  }));
  const daily = dailyFixture(articles[0]);
  const selectionPaths = ["/v1/user/must-reads", "/v1/user/must-reads/presentation"];
  return {
    active: false,
    signedIn: false,
    articles,
    daily,
    failedArticle: true,
    claims: [],
    handles(url) {
      feed.active = this.active;
      return (
        this.active &&
        (selectionPaths.includes(apiPath(url)) ||
          apiPath(url).startsWith("/v1/articles/context-") ||
          feed.handles(url))
      );
    },
    async respond(url, payload) {
      const path = apiPath(url);
      feed.signedIn = this.signedIn;
      if (selectionPaths.includes(path)) {
        const presentation = path.endsWith("/presentation");
        if (presentation) this.claims.push(payload);
        return { status: 200, json: await daily.response(presentation) };
      }
      if (path.startsWith("/v1/articles/context-")) {
        const slug = path.split("/").at(-1);
        const found = articles.find((item) => item.slug === slug);
        if (!found || (slug === articles[3].slug && this.failedArticle))
          return { status: found ? 503 : 404, json: { detail: "Article unavailable" } };
        return {
          status: 200,
          json: url.pathname.startsWith("/api/") ? { article: found, topic: feed.topic } : found,
        };
      }
      const response = feed.respond(url, payload);
      if (["/v1/feed", "/v1/user/feed"].includes(path))
        response.json = { ...response.json, items: articles.slice(0, 3), next_cursor: null };
      return response;
    },
  };
}

/** Reader context follows loaded articles rather than pathname guesses in every runtime. */
export async function checkReaderContext(page, destination, fixture, output, extension = false) {
  await mkdir(output, { recursive: true });
  fixture.active = true;
  const banner = page.getByRole("complementary", { name: "Create a DevFeed account" });
  const article = page.getByRole("dialog", { name: "Article preview", exact: true });
  const mustReads = page.getByRole("dialog", { name: "Today’s Must Reads", exact: true });
  let releaseHeldClaim;
  const visits = () =>
    page.evaluate((key) => JSON.parse(sessionStorage.getItem(key) ?? "[]"), visitsKey);
  const waitVisits = async (count) => {
    await page.waitForFunction(
      ({ key, count }) => JSON.parse(sessionStorage.getItem(key) ?? "[]").length === count,
      { key: visitsKey, count },
    );
  };
  const closeArticle = async () => {
    await article.getByRole("button", { name: "Close preview", exact: true }).click();
    await article.waitFor({ state: "detached" });
  };
  await page.addInitScript(() => {
    if (["http:", "https:", "chrome-extension:"].includes(location.protocol))
      localStorage.setItem("devfeed:dev-card-promo-seen", "true");
  });
  await page.clock.install();
  try {
    // Failed/missing route attempts are not article visits.
    await page.goto(destination("/articles/context-missing"));
    await page.getByRole("heading", { name: /Page not found|Couldn’t load the article/ }).waitFor();
    await page.clock.runFor(100);
    assert.equal((await visits()).length, 0);
    assert.equal(await banner.count(), 0);
    await page.goto(destination(`/articles/${fixture.articles[3].slug}`));
    await article.getByRole("heading", { name: "Couldn’t load the article" }).waitFor();
    await page.clock.runFor(100);
    assert.equal((await visits()).length, 0, "an unavailable article does not count as a visit");

    fixture.failedArticle = false;
    await article.getByRole("button", { name: "Try again", exact: true }).click();
    await article.getByRole("heading", { name: fixture.articles[3].title, exact: true }).waitFor();
    await waitVisits(1);
    assert.equal(await banner.count(), 0, "successful retries count only after article data loads");
    await closeArticle();
    await page.locator(".article-card").first().waitFor();

    // A direct article and a replay of it represent one actual visit.
    await page.goto(destination(`/articles/${fixture.articles[3].slug}`));
    await article.getByRole("heading", { name: fixture.articles[3].title, exact: true }).waitFor();
    await page.clock.runFor(100);
    assert.equal(
      (await visits()).length,
      1,
      "revisiting the same article keeps the distinct count",
    );
    await closeArticle();
    await page.locator(".article-card").first().waitFor();
    await page.locator(".card-open-link").first().click();
    await article.getByRole("heading", { name: fixture.articles[0].title, exact: true }).waitFor();
    await waitVisits(2);
    await page.goBack();
    await article.waitFor({ state: "detached" });
    assert.equal(await banner.count(), 0, "two distinct articles do not trigger signup");
    await page.goForward();
    await article.getByRole("heading", { name: fixture.articles[0].title, exact: true }).waitFor();
    assert.equal((await visits()).length, 2, "history replay does not add another visit");
    await article.getByRole("button", { name: "Next article", exact: true }).click();
    await article.getByRole("heading", { name: fixture.articles[1].title, exact: true }).waitFor();
    await waitVisits(3);
    await page.clock.fastForward(3000);
    assert.equal(await banner.count(), 0, "paging defers signup for the active article");
    await closeArticle();
    await banner.waitFor();
    await page.screenshot({ path: `${output}/signup-after-preview.png`, animations: "disabled" });
    const signup = banner.getByRole("link", { name: "Create account", exact: true });
    if (extension) {
      const target = new URL(await signup.getAttribute("href"));
      assert.equal(target.searchParams.get("return_to"), "/extension/login-complete");
    } else {
      // Query-only navigation can leave the invitation mounted without a new render.
      await page.evaluate(() => {
        history.replaceState(history.state, "", `${location.pathname}?q=reader-context-query`);
      });
      await signup.click();
      await page.waitForURL((url) => url.pathname === "/login");
      assert.equal(
        new URL(page.url()).searchParams.get("return_to"),
        "/?q=reader-context-query",
        "signup captures the current return target when activated",
      );
    }

    for (const path of ["/missing-reader-context", "/login"]) {
      await page.goto(destination(path));
      if (extension || path !== "/login")
        await page.getByRole("heading", { name: "Page not found", exact: true }).waitFor();
      else await page.getByRole("heading", { name: /Sign in/ }).waitFor();
      await page.clock.runFor(100);
      assert.equal(await banner.count(), 0, `${path} does not solicit account signup`);
    }

    fixture.signedIn = true;
    if (!extension)
      await page
        .context()
        .addCookies([{ name: "devfeed_user_session", value: "valid", url: destination("/") }]);
    fixture.daily.enabled = true;
    // A fresh document loads the changed session in extensions as well as the website.
    await page.goto("about:blank");
    await page.goto(destination(`/articles/${fixture.articles[0].slug}`));
    await article.getByRole("heading", { name: fixture.articles[0].title, exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Today’s Must Reads", exact: true })
      .waitFor({ state: "attached" });
    await page.clock.fastForward(6000);
    assert.equal(fixture.claims.length, 0, "a loaded article defers automatic Must Reads claims");
    assert.equal(await mustReads.count(), 0);
    assert.equal(await banner.count(), 0, "signed-in readers never receive signup invitations");

    // Manual requests remain possible while reading, regardless of automatic eligibility.
    const manualResponse = page.waitForResponse(
      (response) => apiPath(new URL(response.url())) === "/v1/user/must-reads/presentation",
    );
    await page.evaluate(() => window.dispatchEvent(new Event("devfeed:open-must-reads")));
    await mustReads.waitFor();
    assert.deepEqual(await (await manualResponse).json(), { claimed: true });
    assert.equal(await mustReads.locator(".article-card").count(), 5);
    assert.equal(fixture.claims.at(-1).automatic, false);
    // Choosing the already-open article still dismisses the daily panel semantically.
    await mustReads.getByRole("link", { name: "Daily Must Read 1", exact: true }).click();
    await mustReads.waitFor({ state: "detached" });
    await article.getByRole("heading", { name: fixture.articles[0].title, exact: true }).waitFor();

    // A claim already in flight must wait for an article opened before the response arrives.
    fixture.daily.presented = false;
    fixture.daily.holdClaim = new Promise((resolve) => {
      releaseHeldClaim = resolve;
    });
    const before = fixture.claims.length;
    const started = page.waitForRequest(
      (request) => apiPath(new URL(request.url())) === "/v1/user/must-reads/presentation",
    );
    await page.goto("about:blank");
    await page.goto(destination("/latest"));
    await page.locator(".article-card").first().waitFor();
    await page.clock.fastForward(3000);
    await started;
    await page.locator(".card-open-link").first().click();
    await article.getByRole("heading", { name: fixture.articles[0].title, exact: true }).waitFor();
    const returned = page.waitForResponse(
      (response) => apiPath(new URL(response.url())) === "/v1/user/must-reads/presentation",
    );
    releaseHeldClaim();
    assert.deepEqual(await (await returned).json(), { claimed: true });
    await page.clock.fastForward(6000);
    assert.equal(await mustReads.count(), 0, "an in-flight successful claim waits for the article");
    assert.equal(
      fixture.claims.length,
      before + 1,
      "pending presentation does not reclaim capacity",
    );
    await closeArticle();
    await page.clock.fastForward(3000);
    await mustReads.waitFor();
    assert.equal(fixture.claims.length, before + 1, "the pending claim resumes after closing");
    await mustReads.screenshot({
      path: `${output}/must-reads-after-preview.png`,
      animations: "disabled",
    });
    await mustReads.getByRole("button", { name: "Close Must Reads", exact: true }).click();
    await mustReads.waitFor({ state: "detached" });
    fixture.daily.holdClaim = null;
    fixture.daily.onClaim = null;

    // A ready daily selection must not auto-open on missing or authorization screens.
    for (const path of ["/missing-reader-context", "/mcp/authorize"]) {
      fixture.daily.presented = false;
      const before = fixture.claims.length;
      await page.goto("about:blank");
      await page.goto(destination(path));
      if (path.includes("missing"))
        await page.getByRole("heading", { name: "Page not found", exact: true }).waitFor();
      else await page.getByRole("heading", { name: "Authorize connection", exact: true }).waitFor();
      await page.clock.fastForward(6000);
      assert.equal(await mustReads.count(), 0, `${path} defers automatic Must Reads`);
      assert.equal(fixture.claims.length, before, `${path} does not claim automatic presentation`);
    }
    console.log(
      "Loaded article visits, prompt eligibility, manual Must Reads, and pending claims passed.",
    );
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error(
      "Reader context failure",
      page.url(),
      await page.locator("body").innerText(),
      fixture.claims,
    );
    throw error;
  } finally {
    releaseHeldClaim?.();
    fixture.active = false;
    fixture.signedIn = false;
    fixture.daily.enabled = false;
    fixture.daily.holdClaim = null;
    fixture.daily.onClaim = null;
  }
}

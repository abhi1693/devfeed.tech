import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import {
  chooseArticleView,
  chooseInlineArticleView,
  openArticleViewSettings,
  readerRouteUrl,
} from "./article-view.mjs";
import { columnsCatalog } from "./columns-workspace.mjs";
import { columnsSharedArticle, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";

const guestKey = "devfeed:feed-views:guest";
const legacyKey = "devfeed:reader-preferences:guest";
const labels = { cards: "Grid", compact: "List", stacked: "Stacked", columns: "Columns" };

/** Real shared reader components save semantic feed identities, not their current route. */
export async function checkIndependentFeedViews(page, target, output, { signedIn = false } = {}) {
  const fixture = await installColumnsFeedFixture(page, { signedIn });
  fixture.setSettings({ view: "cards" });
  const { scopedViews } = fixture;
  const privateRequests = [];
  const record = (request) => {
    if (/\/api\/v1\/user\/settings\/(?:feed-view|feed)(?:\?|$)/.test(request.url()))
      privateRequests.push({ method: request.method(), url: request.url() });
  };
  page.on("request", record);
  // Read later is loaded by its real client component, independently of public feeds.
  const savedPattern = /\/api\/v1\/user\/bookmarks(?:\?|$)/;
  const savedHandler = (route) =>
    route.fulfill({ json: { items: [columnsSharedArticle], next_cursor: null } });
  await page.context().route(savedPattern, savedHandler);
  const route = (path) => readerRouteUrl(target, path);
  const layout = page.getByRole("button", { name: "Change feed layout", exact: true });
  const selectors = {
    cards: ".article-card:visible",
    compact: ".article-list-row:visible",
    stacked: ".article-stacked-row:visible",
  };
  const assertLayout = async (view) => {
    await page.waitForFunction(
      (name) =>
        [...document.querySelectorAll('button[aria-label="Change feed layout"]')].some(
          (button) => !button.disabled && button.title === `Layout: ${name}`,
        ),
      labels[view],
    );
    assert.equal(await layout.count(), 1, "Each feed has one layout control");
    if (view === "columns")
      await page.getByRole("region", { name: "Columns workspace", exact: true }).waitFor();
    else await page.locator(selectors[view]).first().waitFor();
    assert.equal(
      await page.getByRole("region", { name: "Columns workspace", exact: true }).count(),
      view === "columns" ? 1 : 0,
      "Columns is exclusive to the configured signed-in My feed",
    );
  };
  const visit = async (path, view, { reload = false } = {}) => {
    await page.goto(route(path));
    await assertLayout(view);
    if (reload) {
      await page.reload();
      await assertLayout(view);
    }
  };
  const chooseFor = async (path, view, options) => {
    await page.goto(route(path));
    await chooseInlineArticleView(page, view, options);
    await assertLayout(view);
  };
  const appearance = async (feed, view) => {
    await openArticleViewSettings(page, { feed });
    await chooseArticleView(page, view);
  };
  const snapshot = () => ({
    reads: scopedViews.reads.length,
    writes: scopedViews.writes.length,
    legacyWrites: fixture.writes.length,
  });
  const proof = { signedIn, preview: null, scopes: {} };
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(route("/settings/appearance"));
    const legacy = { version: 1, view: "cards", columns_initialized: true, columns: [] };
    await page.evaluate(
      ({ guestKey, legacyKey, legacy }) => {
        localStorage.removeItem(guestKey);
        localStorage.setItem(legacyKey, JSON.stringify(legacy));
      },
      { guestKey, legacyKey, legacy },
    );
    await page.reload();
    const originalGuest = await page.evaluate((key) => localStorage.getItem(key), legacyKey);
    if (signedIn) {
      await appearance("my_feed", "columns");
      await appearance("latest", "stacked");
      const choices = page.getByRole("region", { name: "Article feed layout", exact: true });
      assert.equal(
        await choices.getByRole("radio", { name: "Columns", exact: true }).isDisabled(),
        true,
        "Appearance cannot set Columns on Latest",
      );
      await visit("/", "columns", { reload: true });
      await chooseFor("/read-later", "compact");
      await chooseInlineArticleView(page, "cards");
      await assertLayout("cards");
    } else {
      await appearance("latest", "stacked");
      assert.deepEqual(privateRequests, [], "Guest Appearance never calls private settings APIs");
    }
    await openArticleViewSettings(page, { feed: "latest" });
    await page.screenshot({
      path: `${output}-appearance-desktop.png`,
      fullPage: true,
      animations: "disabled",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: `${output}-appearance-mobile.png`,
      fullPage: true,
      animations: "disabled",
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await chooseFor("/trending", "compact", { keyboard: true });
    await visit("/latest?sort=most_liked", "stacked", { reload: true });
    await visit("/trending", "compact", { reload: true });
    if (signedIn) await visit("/read-later", "cards", { reload: true });
    else await visit("/", "stacked");

    const [first, second] = columnsCatalog.topics;
    const topicA = `/topics/${first.slug}`;
    const topicB = `/topics/${second.slug}`;
    await chooseFor(topicA, "compact");
    await chooseFor(topicB, "stacked");
    await visit(topicA, "compact", { reload: true });
    await visit(topicB, "stacked", { reload: true });
    await visit(`/?topic=${first.slug}`, "compact");
    const source = columnsCatalog.sources[0];
    const tag = columnsCatalog.tags[0];
    await chooseFor(`/sources/${source.slug}`, "stacked");
    await chooseFor(`/tags/${tag.slug}`, "compact");
    await visit(`/?tag=${tag.slug}`, "compact");
    await chooseFor("/articles", "compact");
    await chooseFor("/?q=compiler", "stacked");
    await visit("/?q=runtime", "stacked");
    await visit("/articles", "compact", { reload: true });

    // The foreground article URL must not replace the background feed's scope.
    await visit(topicA, "compact");
    const title = page.locator(".article-list-title:visible").first();
    const row = await page.locator(".article-list-row:visible").first().elementHandle();
    const returnUrl = page.url();
    const beforePreview = snapshot();
    try {
      await title.click();
      const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
      await preview.waitFor();
      assert.equal(await row.evaluate((node) => node.isConnected), true);
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await preview.waitFor({ state: "hidden" });
      await page.waitForURL(returnUrl);
      await assertLayout("compact");
      assert.equal(await row.evaluate((node) => node.isConnected), true);
      assert.deepEqual(snapshot(), beforePreview, "Preview retains the feed scope and preferences");
      proof.preview = { retainedDOM: true, before: beforePreview, after: snapshot(), returnUrl };
    } finally {
      await row.dispose();
    }

    if (signedIn) {
      await visit("/latest", "stacked");
      scopedViews.failNextSave("latest");
      await layout.click();
      await page.getByRole("menuitemradio", { name: "Grid", exact: true }).click();
      await page
        .getByText("Couldn’t save this feed’s layout. Please try again.", { exact: true })
        .waitFor();
      await assertLayout("stacked");
      await page.reload();
      await assertLayout("stacked");
      await visit("/", "columns");
      assert.deepEqual(
        fixture.writes,
        [],
        "Layout saves never overwrite global filters or columns",
      );
      const expected = {
        my_feed: "columns",
        latest: "stacked",
        trending: "compact",
        read_later: "cards",
        [`topic:${first.id}`]: "compact",
        [`topic:${second.id}`]: "stacked",
        [`source:${source.id}`]: "stacked",
        [`tag:${tag.id}`]: "compact",
        "content:article": "compact",
        search: "stacked",
      };
      for (const [feed, view] of Object.entries(expected)) {
        assert.equal(scopedViews.store.read(feed).view, view, `${feed} has its own saved layout`);
        proof.scopes[feed] = view;
      }
      for (const { feed, payload } of scopedViews.writes) {
        assert.deepEqual(Object.keys(payload), ["view"]);
        assert.ok(Object.hasOwn(expected, feed), `Canonical feed key saved: ${feed}`);
      }
      assert.equal(
        await page.evaluate((key) => localStorage.getItem(key), guestKey),
        null,
        "Account scoped layouts never leak into guest localStorage",
      );
    } else {
      const saved = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey);
      assert.equal(saved.version, 1);
      assert.equal(saved.default_view, "cards");
      assert.deepEqual(saved.views, {
        latest: "stacked",
        trending: "compact",
        [`topic:${first.id}`]: "compact",
        [`topic:${second.id}`]: "stacked",
        [`source:${source.id}`]: "stacked",
        [`tag:${tag.id}`]: "compact",
        "content:article": "compact",
        search: "stacked",
      });
      proof.scopes = saved.views;
      assert.deepEqual(
        privateRequests,
        [],
        "Guest scoped changes never call private settings APIs",
      );
      await visit("/latest", "stacked");
      await layout.click();
      assert.equal(
        await page.getByRole("menuitemradio", { name: "Columns", exact: true }).count(),
        0,
      );
      await page.keyboard.press("Escape");
    }
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), legacyKey),
      originalGuest,
      "Scoped saves preserve the complete legacy guest workspace",
    );
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    await layout.click();
    const menu = page.getByRole("menu");
    await menu.waitFor();
    const box = await menu.boundingBox();
    assert.ok(
      box && box.x >= 0 && box.x + box.width <= 391,
      "Mobile layout menu fits the viewport",
    );
    await page.screenshot({ path: `${output}-mobile-menu.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
    proof.writes = scopedViews.writes;
    await writeFile(`${output}.json`, `${JSON.stringify(proof, null, 2)}\n`);
  } catch (error) {
    await page.screenshot({ path: `${output}-failure.png`, animations: "disabled" });
    throw error;
  } finally {
    page.off("request", record);
    await page.context().unroute(savedPattern, savedHandler);
    await fixture.cleanup();
  }
}

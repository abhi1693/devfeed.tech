import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { readerRouteUrl } from "./article-view.mjs";
import { columnsCatalog } from "./columns-workspace.mjs";

export const sessionPreferencesOwner = "11111111-1111-4111-8111-111111111111";
const topics = [
  { ...columnsCatalog.topics[0], name: "Resume TypeScript" },
  { ...columnsCatalog.topics[1], name: "Resume Rust" },
  {
    ...columnsCatalog.topics[0],
    id: "55555555-5555-4555-8555-555555555553",
    name: "Resume Go",
    slug: "resume-go",
  },
];
const wait = async (predicate, message) => {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) {
    assert.ok(Date.now() < deadline, message);
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
};

/** Ordinary same-session resume refreshes remote settings before a later bulk follow save. */
export async function checkSessionPreferences(page, target, output, { csrfToken } = {}) {
  assert.ok(csrfToken);
  const context = page.context();
  const reads = [];
  const writes = [];
  const focusStates = [];
  const focusSessions = [];
  const useNativeFocus = async (targetPage) => {
    const session = await context.newCDPSession(targetPage);
    focusSessions.push(session);
    // Playwright forces every tab to stay focused. Take ownership of that override,
    // then release it so real tab activation can produce browser focus events.
    await session.send("Emulation.setFocusEmulationEnabled", { enabled: true });
    await session.send("Emulation.setFocusEmulationEnabled", { enabled: false });
  };
  const observeFocus = async (phase) =>
    focusStates.push({
      phase,
      ...(await page.evaluate(() => ({
        focused: document.hasFocus(),
        visibility: document.visibilityState,
        now: Date.now(),
      }))),
    });
  let topicIds = [topics[0].id];
  let view = "cards";
  let settings = { view: "cards", columns: [], languages: ["en"], content_types: ["news"] };
  const clockStart = Date.now();
  const identity = {
    user_id: sessionPreferencesOwner,
    name: "Resume Reader",
    email: null,
    csrf_token: csrfToken,
    expires_at: Math.floor(clockStart / 1000) + 3600,
  };
  const pattern = /\/api\/v1\/user\/(?:auth\/me|settings\/(?:feed|feed-view)|preferences)(?:\?|$)/;
  const handler = async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace("/api/v1/user/", "");
    if (request.method() === "GET") reads.push({ endpoint, feed: url.searchParams.get("feed") });
    if (endpoint === "auth/me") return route.fulfill({ json: identity });
    if (endpoint === "settings/feed") return route.fulfill({ json: settings });
    if (endpoint === "settings/feed-view") return route.fulfill({ json: { view } });
    if (request.method() === "PUT") {
      assert.ok(
        (await request.allHeaders())["x-csrf-token"] === csrfToken,
        "Bulk save retains CSRF protection",
      );
      const payload = request.postDataJSON();
      writes.push(structuredClone(payload));
      topicIds = [...payload.topic_ids];
    }
    return route.fulfill({ json: { topic_ids: topicIds } });
  };
  const catalogPattern = /\/api\/v1\/topics(?:\?|$)/;
  const catalog = (route) => route.fulfill({ json: { items: topics, next_cursor: null } });
  await context.route(pattern, handler);
  await context.route(catalogPattern, catalog);
  const count = (endpoint) => reads.filter((read) => read.endpoint === endpoint).length;
  let background;
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.clock.setFixedTime(clockStart);
    await page.goto(readerRouteUrl(target, "/latest"));
    await page.getByRole("button", { name: /^User menu:/ }).waitFor();
    await page.locator(".article-card:visible").first().waitFor();
    await wait(
      () => count("preferences") > 0 && count("settings/feed-view") > 0,
      "The account has loaded its initial follows and scoped layout",
    );
    await useNativeFocus(page);
    await page.bringToFront();
    await page.waitForFunction(() => document.hasFocus());
    await observeFocus("initial");
    const before = {
      auth: count("auth/me"),
      follows: count("preferences"),
      feed: count("settings/feed"),
      layout: count("settings/feed-view"),
    };
    background = await context.newPage();
    await background.goto("about:blank");
    await useNativeFocus(background);
    await background.bringToFront();
    await background.waitForFunction(() => document.hasFocus());
    await page.waitForFunction(() => !document.hasFocus());
    topicIds = [topics[0].id, topics[1].id];
    view = "stacked";
    settings = { ...settings, languages: ["en", "fr"] };
    await page.clock.setFixedTime(clockStart + 61000);
    await observeFocus("background");
    await page.bringToFront();
    await page.waitForFunction(() => document.hasFocus() && document.visibilityState === "visible");
    await observeFocus("resumed");
    await page.locator(".article-stacked-row:visible").first().waitFor();
    await wait(
      () =>
        count("preferences") > before.follows &&
        count("settings/feed") > before.feed &&
        count("settings/feed-view") > before.layout,
      "An ordinary same-session focus refreshes follows, shared settings, and scoped layout",
    );
    assert.ok(count("auth/me") > before.auth, "A genuine focus return rechecks the account");
    assert.deepEqual(writes, [], "Reconciliation never writes a stale local snapshot");
    const after = {
      auth: count("auth/me"),
      follows: count("preferences"),
      feed: count("settings/feed"),
      layout: count("settings/feed-view"),
    };
    await page.evaluate(() => {
      window.__readerPreferenceDocument = document;
    });
    await page.getByRole("button", { name: /^User menu:/ }).click();
    await page.getByRole("menuitem", { name: "Your topics", exact: true }).click();
    const original = page.getByRole("button", { name: topics[0].name, exact: true });
    const remote = page.getByRole("button", { name: topics[1].name, exact: true });
    const added = page.getByRole("button", { name: topics[2].name, exact: true });
    await remote.waitFor();
    assert.ok(
      await page.evaluate(() => window.__readerPreferenceDocument === document),
      "Topic settings retain the existing document and preference providers",
    );
    assert.deepEqual(
      { auth: count("auth/me"), follows: count("preferences"), feed: count("settings/feed") },
      { auth: after.auth, follows: after.follows, feed: after.feed },
      "Client navigation uses the reconciled cache without fetching fresh account preferences",
    );
    assert.equal(await original.getAttribute("aria-pressed"), "true");
    assert.equal(
      await remote.getAttribute("aria-pressed"),
      "true",
      "The externally followed topic is reflected before editing",
    );
    await added.click();
    await page.getByRole("button", { name: "Save topics", exact: true }).click();
    await page.getByText("Your topics are saved.", { exact: true }).waitFor();
    assert.equal(writes.length, 1);
    assert.deepEqual(
      [...writes[0].topic_ids].sort(),
      topics.map(({ id }) => id).sort(),
      "A later bulk save retains the external addition as well as the new local choice",
    );
    await page.screenshot({ path: `${output}.png`, animations: "disabled" });
    await writeFile(
      `${output}.json`,
      `${JSON.stringify({ passed: true, before, after, focusStates, reads, writes }, null, 2)}\n`,
    );
  } catch (error) {
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    await writeFile(
      `${output}-failure.json`,
      `${JSON.stringify({ error: error.message, focusStates, reads, writes }, null, 2)}\n`,
    );
    throw error;
  } finally {
    for (const session of focusSessions) {
      await session.send("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
      await session.detach().catch(() => {});
    }
    await background?.close();
    await context.unroute(pattern, handler);
    await context.unroute(catalogPattern, catalog);
  }
}

/** Actual server document nodes must exist before client preferences or alias lookup can run. */
export async function checkPublicFeedDocument(page, target) {
  const response = await page.goto(target);
  assert.equal(response.status(), 200);
  const html = await response.text();
  console.info("Public document response", {
    route: new URL(target).pathname + new URL(target).search,
    articles: (html.match(/<article\b/g) ?? []).length,
    links: (html.match(/class="card-open-link"/g) ?? []).length,
    domArticles: await page.locator(".article-card").count(),
  });
  const links = page.locator(".article-card .card-open-link");
  await links.first().waitFor({ state: "attached" });
  assert.equal(
    await links.count(),
    24,
    "The no-JavaScript document includes the public article subtree",
  );
  const href = await links.first().getAttribute("href");
  assert.match(href, /\/articles\/preview-retention-0$/);
  assert.match(
    html,
    /<article\b[^>]*class="[^"]*\barticle-card\b/,
    "Article content exists as server HTML rather than only Flight data",
  );
  assert.equal(await page.locator('.pagination[data-has-more="true"]').count(), 1);
  const data = await page
    .locator('script[type="application/ld+json"]')
    .evaluateAll((nodes) => nodes.map((node) => JSON.parse(node.textContent)));
  const collection = data.find((entry) => entry.mainEntity?.["@type"] === "ItemList");
  assert.equal(
    collection?.mainEntity.itemListElement.length,
    24,
    "ItemList JSON-LD includes the server-rendered articles",
  );
  assert.match(collection.mainEntity.itemListElement[0].url, /\/articles\/preview-retention-0$/);
  return {
    route: new URL(target).pathname + new URL(target).search,
    articles: await links.count(),
    pagination: true,
    structuredArticles: collection.mainEntity.itemListElement.length,
  };
}

import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";

const guestKey = "devfeed:reader-preferences:guest";
const legacyKey = "devfeed:feed-view:guest";
const uuid = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/;
const catalog = {
  sources: [
    {
      id: "33333333-3333-4333-8333-333333333331",
      name: "Column Source",
      slug: "column-source",
      website_url: "https://publisher.test",
      logo_url: null,
      description: "A source available to the reader workspace.",
    },
  ],
  topics: [
    {
      id: "55555555-5555-4555-8555-555555555551",
      name: "Column TypeScript",
      slug: "column-typescript",
      kind: "language",
      logo_url: null,
      website_url: null,
      description: "Typed JavaScript",
      ai_description: null,
    },
    {
      id: "55555555-5555-4555-8555-555555555552",
      name: "Column Rust",
      slug: "column-rust",
      kind: "language",
      logo_url: null,
      website_url: null,
      description: "Systems programming",
      ai_description: null,
    },
  ],
  tags: [
    {
      id: "66666666-6666-4666-8666-666666666661",
      name: "Column Engineering",
      slug: "column-engineering",
    },
  ],
};

/** Configure the real reader workspace against a finite public catalog and persisted settings. */
export async function checkColumnsWorkspace(page, target, output, { signedIn = false } = {}) {
  const context = page.context();
  const writes = [];
  const unavailable = new Set();
  const feedReads = [];
  let account = {
    view: "cards",
    columns: [],
    content_types: ["news", "article"],
    languages: ["en"],
  };
  const accountPattern = /\/api\/v1\/user\/settings\/feed(?:\?|$)/;
  const accountHandler = async (route) => {
    const request = route.request();
    if (!signedIn) {
      assert.equal(request.method(), "GET", "Guests never save account preferences");
      return route.fallback();
    }
    if (request.method() === "PUT") {
      assert.ok((await request.allHeaders())["x-csrf-token"], "Account writes retain CSRF proof");
      account = structuredClone(request.postDataJSON());
      writes.push(structuredClone(account));
    }
    return route.fulfill({ json: account });
  };
  const catalogPattern = /\/api\/v1\/(topics|sources|tags)(?:\/[^/?]+)?(?:\?|$)/;
  const catalogHandler = async (route) => {
    const url = new URL(route.request().url());
    const [, kind, key] = url.pathname.match(/\/api\/v1\/(topics|sources|tags)(?:\/([^/]+))?$/);
    const items = catalog[kind].filter((item) => !unavailable.has(item.id));
    if (key) {
      const item = items.find((item) => item.id === key || item.slug === decodeURIComponent(key));
      return route.fulfill({ status: item ? 200 : 404, json: item ?? { detail: "Not found" } });
    }
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    return route.fulfill({
      json: {
        items: items.filter((item) => item.name.toLowerCase().includes(query)),
        next_cursor: null,
      },
    });
  };
  const recordFeedRead = (request) => {
    if (/\/api\/v1\/(?:user\/)?feed(?:\?|$)/.test(request.url())) feedReads.push(request.url());
  };
  const saved = async () =>
    signedIn
      ? structuredClone(account)
      : page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey);
  const selectLayout = async (name) => {
    const layout = await openArticleViewSettings(page);
    await layout.getByRole("radio", { name, exact: true }).check();
    await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
    await page.goto(target);
  };
  const workspace = () => page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = () => workspace().locator("[data-column-id]");
  const identities = () =>
    columns().evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  const waitOrder = async (expected) => {
    await page.waitForFunction(
      (expected) =>
        JSON.stringify(
          [...document.querySelectorAll("[data-column-id]")].map((node) => node.dataset.columnId),
        ) === JSON.stringify(expected),
      expected,
    );
    assert.deepEqual(
      (await saved()).columns.map((column) => column.id),
      expected,
    );
  };
  const chooseFeed = async (dialog, kind, entity) => {
    await dialog.getByRole("combobox", { name: "Feed type", exact: true }).selectOption(kind);
    if (entity) {
      await dialog.getByLabel(`Find a ${kind}`, { exact: true }).fill(entity.name);
      await dialog.getByRole("button", { name: entity.name, exact: true }).click();
    }
    await dialog.getByRole("button", { name: "Save column", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
  };
  const add = async (kind, entity) => {
    const count = await columns().count();
    await workspace().getByRole("button", { name: "Add column", exact: true }).click();
    await chooseFeed(page.getByRole("dialog", { name: "Add column", exact: true }), kind, entity);
    await page.waitForFunction(
      (count) => document.querySelectorAll("[data-column-id]").length === count + 1,
      count,
    );
  };
  await context.route(accountPattern, accountHandler);
  await context.route(catalogPattern, catalogHandler);
  page.on("request", recordFeedRead);
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    await openArticleViewSettings(page);
    if (!signedIn) {
      await page.evaluate(
        ({ guestKey, legacyKey }) => {
          localStorage.removeItem(guestKey);
          localStorage.removeItem(legacyKey);
        },
        { guestKey, legacyKey },
      );
      await page.reload();
    }
    const guestBefore = await page.evaluate((key) => localStorage.getItem(key), guestKey);
    await selectLayout("Columns");
    await workspace().getByRole("heading", { name: "Your columns", exact: true }).waitFor();
    assert.equal(await columns().count(), 0, "An empty workspace never invents columns");
    const initialFeedReads = feedReads.length;
    await add("latest");
    await add("recommended");
    await add("source", catalog.sources[0]);
    await add("topic", catalog.topics[0]);
    await add("tag", catalog.tags[0]);
    const originalIds = await identities();
    assert.equal(new Set(originalIds).size, 5, "Each independent column has a unique identity");
    for (const id of originalIds) assert.match(id, uuid, "Column identities use persisted UUIDs");
    assert.deepEqual((await saved()).columns, [
      { id: originalIds[0], feed: { kind: "latest" } },
      { id: originalIds[1], feed: { kind: "recommended" } },
      { id: originalIds[2], feed: { kind: "source", id: catalog.sources[0].id } },
      { id: originalIds[3], feed: { kind: "topic", id: catalog.topics[0].id } },
      { id: originalIds[4], feed: { kind: "tag", id: catalog.tags[0].id } },
    ]);
    if (!signedIn)
      await columns()
        .nth(1)
        .getByText("Sign in to view recommendations", { exact: false })
        .waitFor();

    await workspace().getByRole("button", { name: "Edit Latest column", exact: true }).click();
    await chooseFeed(
      page.getByRole("dialog", { name: "Edit column", exact: true }),
      "topic",
      catalog.topics[1],
    );
    await workspace().getByRole("heading", { name: catalog.topics[1].name, exact: true }).waitFor();
    assert.deepEqual(
      await identities(),
      originalIds,
      "Editing a selection retains column identities",
    );
    assert.deepEqual((await saved()).columns[0], {
      id: originalIds[0],
      feed: { kind: "topic", id: catalog.topics[1].id },
    });

    const moveTag = workspace().getByRole("button", {
      name: `Move ${catalog.tags[0].name} left`,
      exact: true,
    });
    await moveTag.focus();
    await page.keyboard.press("Enter");
    const reordered = [
      originalIds[0],
      originalIds[1],
      originalIds[2],
      originalIds[4],
      originalIds[3],
    ];
    await waitOrder(reordered);
    await workspace()
      .getByRole("button", { name: `Remove ${catalog.sources[0].name} column`, exact: true })
      .click();
    const remaining = [originalIds[0], originalIds[1], originalIds[4], originalIds[3]];
    await waitOrder(remaining);
    assert.equal(
      feedReads.length,
      initialFeedReads,
      "Configuring independent selections never starts per-column article fetching",
    );
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    const configured = (await saved()).columns;

    await selectLayout("Grid");
    await page.locator(".article-card:visible").first().waitFor();
    assert.deepEqual((await saved()).columns, configured, "Selecting Grid retains the workspace");
    await selectLayout("Columns");
    await waitOrder(remaining);
    if (signedIn) {
      assert.equal(
        await page.evaluate((key) => localStorage.getItem(key), guestKey),
        guestBefore,
        "Account configuration leaves the guest workspace intact",
      );
      await page.evaluate(() => localStorage.clear());
    }
    await page.reload();
    await workspace().waitFor();
    await waitOrder(remaining);
    assert.deepEqual(
      (await saved()).columns,
      configured,
      "Saved column selections survive a reload",
    );
    if (signedIn) {
      assert.ok(writes.length >= 9, "Account workspace changes save through the user API");
      assert.equal(
        await page.evaluate((key) => localStorage.getItem(key), guestKey),
        null,
        "Reloading account settings never seeds guest storage",
      );
    } else assert.equal(writes.length, 0, "The guest workspace never saves to an account");

    await page.setViewportSize({ width: 390, height: 844 });
    const navigation = workspace().getByRole("combobox", { name: "Go to column", exact: true });
    await navigation.selectOption(remaining.at(-1));
    const selectedColumn = workspace().locator(`[data-column-id="${remaining.at(-1)}"]`);
    await page.waitForFunction(
      (id) => {
        const bounds = document.querySelector(`[data-column-id="${id}"]`)?.getBoundingClientRect();
        return bounds && bounds.left >= -1 && bounds.right <= innerWidth + 1;
      },
      remaining.at(-1),
      { timeout: 5000 },
    );
    assert.ok(
      (await selectedColumn.boundingBox()).width >= 280,
      "Mobile columns retain a usable card width instead of shrinking all feeds into the viewport",
    );
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
      "Mobile columns scroll inside the workspace without widening the page",
    );
    const keyboardMove = selectedColumn.getByRole("button", {
      name: `Move ${catalog.topics[0].name} left`,
      exact: true,
    });
    await keyboardMove.focus();
    await page.keyboard.press("Enter");
    const mobileOrder = [remaining[0], remaining[1], remaining[3], remaining[2]];
    await waitOrder(mobileOrder);
    await page.waitForFunction(
      (id) => {
        const column = document.querySelector(`[data-column-id="${id}"]`);
        const bounds = column?.getBoundingClientRect();
        return (
          bounds &&
          bounds.left >= -1 &&
          bounds.right <= innerWidth + 1 &&
          document.activeElement === column
        );
      },
      remaining.at(-1),
      { timeout: 5000 },
    );
    await page.screenshot({ path: `${output}-mobile.png`, animations: "disabled" });

    unavailable.add(catalog.topics[1].id);
    await page.reload();
    const missing = workspace().locator(`[data-column-id="${originalIds[0]}"]`);
    await missing.getByRole("heading", { name: "Unavailable topic", exact: true }).waitFor();
    assert.deepEqual(
      (await saved()).columns.find((column) => column.id === originalIds[0]).feed,
      { kind: "topic", id: catalog.topics[1].id },
      "An unavailable selection is retained instead of being silently replaced",
    );
    assert.equal(
      await missing
        .getByRole("button", { name: "Remove Unavailable topic column", exact: true })
        .isEnabled(),
      true,
      "An unavailable column can be removed",
    );
    const editMissing = missing.getByRole("button", {
      name: "Edit Unavailable topic column",
      exact: true,
    });
    await editMissing.focus();
    await page.keyboard.press("Enter");
    await chooseFeed(page.getByRole("dialog", { name: "Edit column", exact: true }), "latest");
    await missing.getByRole("heading", { name: "Latest", exact: true }).waitFor();
    assert.equal(
      (await saved()).columns[0].id,
      originalIds[0],
      "Repairing an unavailable column retains its identity",
    );
    const repaired = (await saved()).columns;
    await missing.getByRole("button", { name: "Open Latest feed", exact: true }).click();
    await page.waitForURL(readerRouteUrl(target, "/latest"));
    await page.locator(".article-card:visible").first().waitFor();
    assert.equal((await saved()).view, "cards", "Opening a selected feed displays its articles");
    assert.deepEqual((await saved()).columns, repaired, "Opening a feed retains the workspace");
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        { signedIn, originalIds, configured, saved: await saved(), accountWrites: writes.length },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    console.error("Columns workspace failure", page.url(), await page.locator("body").innerText());
    throw error;
  } finally {
    page.off("request", recordFeedRead);
    await context.unroute(accountPattern, accountHandler);
    await context.unroute(catalogPattern, catalogHandler);
  }
}

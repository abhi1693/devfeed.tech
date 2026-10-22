import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";
import { createFixtureFeedViews, installFixtureFeedViews } from "./feed-view-fixture.mjs";
import { reorderColumn } from "./column-reorder.mjs";

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
export { catalog as columnsCatalog };

export async function openColumnMenu(page, column, { keyboard = false } = {}) {
  const trigger = column.getByRole("button", { name: /^Options for .+ column$/ });
  if (keyboard) {
    await trigger.focus();
    await page.keyboard.press("Enter");
  } else await trigger.click();
  const menu = page.getByRole("menu");
  await menu.waitFor();
  return menu;
}

export async function columnAction(page, column, name, { keyboard = false } = {}) {
  const menu = await openColumnMenu(page, column, { keyboard });
  const item = menu.getByRole("menuitem", { name, exact: typeof name === "string" });
  if (keyboard) {
    await item.focus();
    await page.keyboard.press("Enter");
  } else await item.click();
  await menu.waitFor({ state: "hidden" });
}

/** Saved guest Columns preferences stay intact while all guest routes use the normal feed. */
export async function checkGuestColumnsUnavailable(page, target, output) {
  const privateReads = [];
  const record = (request) => {
    if (
      /\/api\/v1\/user\/(?:settings\/(?:feed(?:-view)?|columns)|feed)(?:\/|\?|$)/.test(
        request.url(),
      )
    )
      privateReads.push({ url: request.url(), method: request.method() });
  };
  page.on("request", record);
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    const legacy = {
      version: 1,
      view: "columns",
      columns_initialized: true,
      columns: [
        { id: "f0000000-0000-4000-8000-000000000001", feed: { kind: "latest" } },
        {
          id: "f0000000-0000-4000-8000-000000000002",
          feed: { kind: "topic", id: catalog.topics[0].id },
        },
      ],
    };
    await page.evaluate(({ key, value }) => localStorage.setItem(key, JSON.stringify(value)), {
      key: guestKey,
      value: legacy,
    });
    await page.reload();
    const layout = await openArticleViewSettings(page);
    assert.equal(
      await layout.getByRole("radio", { name: "Columns", exact: true }).isDisabled(),
      true,
    );
    await layout.getByRole("link", { name: "Sign in to use Columns", exact: true }).waitFor();
    assert.equal(await layout.getByRole("radio", { name: "Grid", exact: true }).isChecked(), true);
    for (const route of [
      "/",
      "/latest",
      "/trending",
      "/articles",
      `/?tag=${catalog.tags[0].slug}`,
    ]) {
      await page.goto(readerRouteUrl(target, route));
      await page.locator(".theme-toggle:not([disabled])").waitFor();
      await page.locator(".article-card:visible").first().waitFor();
      assert.equal(
        await page.getByRole("region", { name: "Columns workspace", exact: true }).count(),
        0,
      );
    }
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${output}-mobile.png`, animations: "disabled" });
    assert.deepEqual(
      await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey),
      legacy,
    );
    assert.deepEqual(privateReads, [], "Guests never load account settings or personalized feeds");
    await writeFile(
      `${output}.json`,
      JSON.stringify({ passed: true, legacy, privateReads }, null, 2),
    );
  } finally {
    page.off("request", record);
  }
}

/** Configure the real reader workspace against a finite public catalog and persisted settings. */
export async function checkColumnsWorkspace(
  page,
  target,
  output,
  { signedIn = false, nativeInsecureContext = false } = {},
) {
  assert.equal(signedIn, true, "Only authenticated My feed exposes the workspace editor");
  const context = page.context();
  const writes = [];
  const unavailable = new Set();
  const feedReads = [];
  const nativeScrolls = [];
  const routeScopes = [];
  const menuRemoval = {
    desktopTargets: [],
    touchTarget: null,
    focusTransferred: false,
    emptyAfterReload: false,
  };
  let touchSession = null;
  let account = {
    view: "cards",
    columns: [{ id: "ffffffff-ffff-4fff-8fff-ffffffffffff", feed: { kind: "latest" } }],
    content_types: ["news", "article"],
    languages: ["en"],
  };
  let personalColumns = [];
  const scopedViews = await installFixtureFeedViews(page, {
    store: createFixtureFeedViews(() => account.view),
    isSignedIn: () => signedIn,
    onSave: () =>
      writes.push({ ...structuredClone(account), columns: structuredClone(personalColumns) }),
  });
  const preservedLatest = structuredClone(account.columns);
  const receiptColumns = (columns) => {
    const seen = new Set();
    return columns.filter(({ feed }) => {
      const key = `${feed.kind}:${feed.id ?? ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };
  const accountPattern = /\/api\/v1\/user\/settings\/(?:feed|columns)(?:\?|$)/;
  const accountHandler = async (route) => {
    const request = route.request();
    if (!signedIn) {
      assert.equal(request.method(), "GET", "Guests never save account preferences");
      return route.fallback();
    }
    const url = new URL(request.url());
    if (url.pathname.endsWith("/columns")) {
      if (url.searchParams.get("workspace") !== "my_feed")
        return route.fulfill({ json: { initialized: true, columns: account.columns } });
      if (request.method() === "PUT") {
        assert.ok((await request.allHeaders())["x-csrf-token"], "Account writes retain CSRF proof");
        personalColumns = structuredClone(request.postDataJSON().columns);
        writes.push({ ...structuredClone(account), columns: structuredClone(personalColumns) });
      }
      return route.fulfill({
        json: { initialized: true, columns: receiptColumns(personalColumns) },
      });
    }
    if (request.method() === "PUT") {
      assert.ok((await request.allHeaders())["x-csrf-token"], "Account writes retain CSRF proof");
      account = { ...account, ...structuredClone(request.postDataJSON()) };
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
      ? {
          ...structuredClone(account),
          view: scopedViews.store.read("my_feed").view,
          columns: structuredClone(personalColumns),
        }
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
  if (!nativeInsecureContext) {
    const disableRandomUuid = () =>
      Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
    await page.addInitScript(disableRandomUuid);
    await page.evaluate(disableRandomUuid);
  }
  page.on("request", recordFeedRead);
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    await openArticleViewSettings(page);
    if (!signedIn) {
      await page.evaluate(
        ({ guestKey, legacyKey }) => {
          localStorage.setItem(
            guestKey,
            JSON.stringify({ version: 1, view: "cards", columns: [], columns_initialized: true }),
          );
          localStorage.removeItem(legacyKey);
        },
        { guestKey, legacyKey },
      );
      await page.reload();
    }
    const guestBefore = await page.evaluate((key) => localStorage.getItem(key), guestKey);
    const cryptoCapabilities = await page.evaluate(() => ({
      secureContext: isSecureContext,
      randomUUID: typeof crypto.randomUUID,
      getRandomValues: typeof crypto.getRandomValues,
      origin: location.origin,
    }));
    assert.equal(cryptoCapabilities.randomUUID, "undefined");
    assert.equal(cryptoCapabilities.getRandomValues, "function");
    if (nativeInsecureContext)
      assert.equal(
        cryptoCapabilities.secureContext,
        false,
        "The HTTP hostname exercises the browser's native insecure-context capability boundary",
      );
    await selectLayout("Columns");
    await workspace().getByRole("heading", { name: "My feed", level: 1, exact: true }).waitFor();
    for (const text of [
      "Browse your feeds side by side. Each column loads independently.",
      "Your reading workspace",
    ])
      assert.equal(
        await workspace().getByText(text, { exact: true }).count(),
        0,
        "The workspace header omits helper subtitles",
      );
    assert.equal(await columns().count(), 0, "An empty workspace never invents columns");
    const initialFeedReads = feedReads.length;
    assert.equal(
      await workspace().getByRole("combobox", { name: "Go to column", exact: true }).count(),
      0,
    );
    assert.equal(
      await workspace()
        .getByText(/^\d+ of 12 columns$/)
        .count(),
      0,
    );
    await add("latest");
    await add("recommended");
    await add("source", catalog.sources[0]);
    await add("topic", catalog.topics[0]);
    await add("tag", catalog.tags[0]);
    const originalIds = await identities();
    assert.equal(new Set(originalIds).size, 5, "Each independent column has a unique identity");
    for (const id of originalIds) assert.match(id, uuid, "Column identities use persisted UUIDs");
    assert.equal(
      await workspace()
        .getByRole("button", { name: /^Refresh .+ column$/ })
        .count(),
      0,
      "Column headers have no refresh button",
    );
    for (const column of await columns().all()) {
      const header = await column.evaluate((node) => {
        const heading = node.firstElementChild.querySelector("h2");
        return {
          kind: node.dataset.feedKind,
          name: heading.textContent.trim(),
          title: heading.title,
          nameText: heading.parentElement.textContent.trim(),
          controls: [...node.firstElementChild.querySelectorAll("button")].map((button) =>
            button.getAttribute("aria-label"),
          ),
        };
      });
      assert.deepEqual(
        header.controls,
        [`Reorder ${header.name} column`, `Options for ${header.name} column`],
        "Column names retain their accessible Reorder and Options controls",
      );
      assert.equal(
        header.title,
        header.name,
        "The full feed name remains available on the heading",
      );
      if (["source", "topic", "tag"].includes(header.kind))
        assert.equal(header.nameText, header.name, "Column names have no feed-kind subtitle");
    }
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
        .getByText("Sign in to view recommendations.", { exact: true })
        .waitFor();

    for (const column of await columns().all())
      assert.equal(
        await column.getByRole("button", { name: /^Remove .+ column$/ }).count(),
        0,
        "Remove is available through Options instead of a separate header button",
      );

    const latestColumn = columns().first();
    const options = latestColumn.getByRole("button", {
      name: "Options for Latest column",
      exact: true,
    });
    const optionsBounds = await options.boundingBox();
    assert.ok(optionsBounds.width >= 34 && optionsBounds.height >= 34);
    const keyboardMenu = await openColumnMenu(page, latestColumn, { keyboard: true });
    assert.equal(await keyboardMenu.getByRole("menuitem").count(), 3);
    assert.equal(
      await keyboardMenu.getByRole("menuitem", { name: /^Move (left|right)/ }).count(),
      0,
      "Column menus omit the replaced movement actions",
    );
    for (const item of await keyboardMenu.getByRole("menuitem").all())
      assert.ok((await item.boundingBox()).height >= 38, "Desktop menu actions remain readable");
    const desktopRemoveBounds = await keyboardMenu
      .getByRole("menuitem", { name: "Remove column: Latest", exact: true })
      .boundingBox();
    menuRemoval.desktopTargets.push({
      width: desktopRemoveBounds.width,
      height: desktopRemoveBounds.height,
    });
    await page.keyboard.press("Escape");
    await keyboardMenu.waitFor({ state: "hidden" });
    await page.waitForFunction(
      (button) => document.activeElement === button,
      await options.elementHandle(),
    );
    assert.equal(await options.evaluate((node) => document.activeElement === node), true);

    const duplicateWrites = writes.length;
    await workspace().getByRole("button", { name: "Add column", exact: true }).click();
    const duplicateDialog = page.getByRole("dialog", { name: "Add column", exact: true });
    const duplicateTypes = duplicateDialog.getByRole("combobox", {
      name: "Feed type",
      exact: true,
    });
    for (const kind of ["latest", "recommended"])
      assert.equal(
        await duplicateTypes
          .locator(`option[value="${kind}"]`)
          .evaluate((option) => option.disabled),
        true,
        `Existing ${kind} cannot be added again`,
      );
    await duplicateTypes.selectOption("topic");
    await duplicateDialog.getByLabel("Find a topic", { exact: true }).fill(catalog.topics[0].name);
    const addedTopic = duplicateDialog.getByRole("button", {
      name: `${catalog.topics[0].name} Added`,
      exact: true,
    });
    await addedTopic.waitFor();
    assert.equal(await addedTopic.isDisabled(), true, "Existing catalog feeds are marked Added");
    assert.match(await addedTopic.innerText(), /Added/);
    assert.equal(
      await duplicateDialog.getByRole("button", { name: "Save column", exact: true }).isDisabled(),
      true,
    );
    await duplicateDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    assert.deepEqual(await identities(), originalIds);
    assert.equal(writes.length, duplicateWrites, "Rejected duplicate choices never save settings");

    await columnAction(page, latestColumn, "Change feed for Latest column");
    const editDialog = page.getByRole("dialog", { name: "Edit column", exact: true });
    const editTypes = editDialog.getByRole("combobox", { name: "Feed type", exact: true });
    assert.equal(
      await editTypes.locator('option[value="latest"]').evaluate((option) => option.disabled),
      false,
    );
    assert.equal(
      await editTypes.locator('option[value="recommended"]').evaluate((option) => option.disabled),
      true,
    );
    await editTypes.selectOption("topic");
    await editDialog.getByLabel("Find a topic", { exact: true }).fill(catalog.topics[0].name);
    const existingTopic = editDialog.getByRole("button", {
      name: `${catalog.topics[0].name} Added`,
      exact: true,
    });
    await existingTopic.waitFor();
    assert.equal(await existingTopic.isDisabled(), true, "Editing cannot duplicate another feed");
    await chooseFeed(editDialog, "topic", catalog.topics[1]);
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

    await reorderColumn(page, columns().last(), -1);
    const reordered = [
      originalIds[0],
      originalIds[1],
      originalIds[2],
      originalIds[4],
      originalIds[3],
    ];
    await waitOrder(reordered);
    await columnAction(
      page,
      columns().filter({
        has: page.getByRole("heading", { name: catalog.sources[0].name, exact: true }),
      }),
      `Remove column: ${catalog.sources[0].name}`,
    );
    const remaining = [originalIds[0], originalIds[1], originalIds[4], originalIds[3]];
    await waitOrder(remaining);
    await page.waitForFunction(
      (id) => document.activeElement?.getAttribute("data-column-id") === id,
      originalIds[4],
    );
    menuRemoval.focusTransferred = true;
    assert.ok(
      feedReads.length > initialFeedReads,
      "Configured selections start their independently loaded feeds",
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

    if (!nativeInsecureContext) {
      for (const [route, heading] of [
        ["/latest", "Latest feed"],
        [`/topics/${catalog.topics[0].slug}`, catalog.topics[0].name],
        [`/sources/${catalog.sources[0].slug}`, catalog.sources[0].name],
        [`/tags/${catalog.tags[0].slug}`, catalog.tags[0].name],
        [`/topics/${catalog.topics[0].slug}/articles`, catalog.topics[0].name],
        [`/latest?source_id=${catalog.sources[0].id}`, catalog.sources[0].name],
        [`/?tag=${encodeURIComponent(catalog.tags[0].slug)}`, null],
        ["/trending", "Trending"],
        ["/articles", null],
        ...(signedIn ? [["/read-later", "Read later"]] : []),
      ]) {
        await page.goto(readerRouteUrl(target, route));
        await page.locator(".theme-toggle:not([disabled])").waitFor();
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        if (heading) await page.getByRole("heading", { name: heading, exact: true }).waitFor();
        if (route !== "/read-later") await page.locator(".article-card:visible").first().waitFor();
        assert.equal(await workspace().count(), 0, `${route} renders its own feed`);
        assert.equal((await saved()).view, "columns", "A route fallback preserves the saved view");
        assert.deepEqual(
          (await saved()).columns,
          configured,
          "A route fallback preserves the workspace",
        );
        routeScopes.push(route);
      }
      await page.screenshot({ path: `${output}-scoped-feed.png`, animations: "disabled" });
      await page.goto(readerRouteUrl(target, "/"));
      await workspace().waitFor();
      assert.deepEqual(
        (await saved()).columns,
        configured,
        "Returning to My feed retains the saved personal workspace",
      );
      await page.goto(target);
      await workspace().waitFor();
      await waitOrder(remaining);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    touchSession = await context.newCDPSession(page);
    await touchSession.send("Emulation.setTouchEmulationEnabled", {
      enabled: true,
      maxTouchPoints: 1,
    });
    assert.equal(await page.evaluate(() => matchMedia("(pointer: coarse)").matches), true);
    const board = workspace().getByRole("region", { name: "Column feeds", exact: true });
    await board.evaluate((node) => (node.scrollLeft = 0));
    await board.focus();
    for (let index = 0; index < 8; index++) await page.keyboard.press("ArrowRight");
    await page.waitForFunction(
      () => document.querySelector('[aria-label="Column feeds"]').scrollLeft > 0,
    );
    await board.evaluate(
      (node) =>
        new Promise((resolve) => {
          let previous = node.scrollLeft;
          let stable = performance.now();
          const settled = () => {
            if (node.scrollLeft !== previous) {
              previous = node.scrollLeft;
              stable = performance.now();
            }
            if (performance.now() - stable >= 150) resolve();
            else requestAnimationFrame(settled);
          };
          requestAnimationFrame(settled);
        }),
    );
    const selectedColumn = workspace().locator(`[data-column-id="${remaining.at(-1)}"]`);
    const recordScroll = async (action) => {
      nativeScrolls.push(
        await board.evaluate((node, action) => {
          const last = node.lastElementChild.getBoundingClientRect();
          return {
            action,
            scrollLeft: node.scrollLeft,
            clientWidth: node.clientWidth,
            scrollWidth: node.scrollWidth,
            last: { left: last.left, right: last.right },
          };
        }, action),
      );
    };
    await recordScroll("ArrowRight");
    await board.hover({ position: { x: 120, y: 20 } });
    for (let attempt = 0; attempt < 6; attempt++) {
      await page.mouse.wheel(500, 0);
      await page.waitForTimeout(150);
      await recordScroll("horizontal wheel");
      if (
        await selectedColumn.evaluate((node) => {
          const bounds = node.getBoundingClientRect();
          return bounds.left >= -1 && bounds.right <= innerWidth + 1;
        })
      )
        break;
    }
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
    const mobileOptions = selectedColumn.getByRole("button", { name: /^Options for .+ column$/ });
    assert.equal(
      await workspace()
        .getByRole("button", { name: /^Refresh .+ column$/ })
        .count(),
      0,
      "Touch column headers have no refresh button",
    );
    const mobileBounds = await mobileOptions.boundingBox();
    assert.ok(
      mobileBounds.width >= 44 && mobileBounds.height >= 44,
      "Touch column controls retain44px targets",
    );
    const mobileMenu = await openColumnMenu(page, selectedColumn);
    const touchRemove = mobileMenu.getByRole("menuitem", { name: /^Remove column: / });
    const touchBounds = await touchRemove.boundingBox();
    assert.ok(
      touchBounds.width >= 44 && touchBounds.height >= 44,
      "The Remove menu item retains a44px touch target",
    );
    menuRemoval.touchTarget = { width: touchBounds.width, height: touchBounds.height };
    for (const item of await mobileMenu.getByRole("menuitem").all())
      assert.ok((await item.boundingBox()).height >= 44, "Touch menu actions retain44px targets");
    await page.keyboard.press("Escape");
    await mobileMenu.waitFor({ state: "hidden" });
    const mobileActions = await selectedColumn
      .locator(".article-stacked-row")
      .first()
      .evaluate((row) =>
        [...row.querySelectorAll(".article-quick-actions button,.article-quick-actions a")]
          .filter((node) => node.getClientRects().length)
          .map((node) => ({
            width: node.getBoundingClientRect().width,
            height: node.getBoundingClientRect().height,
          })),
      );
    assert.ok(
      mobileActions.every(({ width, height }) => width >= 44 && height >= 44),
      "Touch article actions retain44px targets",
    );
    await reorderColumn(page, selectedColumn, -1);
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
          document.activeElement === column.querySelector("[data-column-drag-handle]")
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
    const unavailableMenu = await openColumnMenu(page, missing, { keyboard: true });
    assert.equal(
      await unavailableMenu
        .getByRole("menuitem", { name: "Remove column: Unavailable topic", exact: true })
        .isEnabled(),
      true,
      "An unavailable column can be removed",
    );
    const editMissing = unavailableMenu.getByRole("menuitem", {
      name: "Change feed for Unavailable topic column",
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
    await missing.locator(".article-stacked-row").first().waitFor();
    assert.equal((await saved()).view, "columns", "A repaired feed loads within its column");

    const uniqueColumns = structuredClone((await saved()).columns);
    const legacyDuplicates = [
      uniqueColumns[0],
      { ...uniqueColumns[0], id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1" },
      ...uniqueColumns.slice(1),
      { ...uniqueColumns.at(-1), id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee2" },
    ];
    if (signedIn) personalColumns = legacyDuplicates;
    else
      await page.evaluate(
        ({ key, columns }) => {
          const current = JSON.parse(localStorage.getItem(key));
          localStorage.setItem(key, JSON.stringify({ ...current, columns }));
        },
        { key: guestKey, columns: legacyDuplicates },
      );
    await page.reload();
    await page.waitForFunction(
      (expected) =>
        JSON.stringify(
          [...document.querySelectorAll("[data-column-id]")].map((node) => node.dataset.columnId),
        ) === JSON.stringify(expected),
      uniqueColumns.map(({ id }) => id),
    );
    await columnAction(page, columns().first(), "Change feed for Latest column");
    await chooseFeed(
      page.getByRole("dialog", { name: "Edit column", exact: true }),
      "source",
      catalog.sources[0],
    );
    assert.deepEqual(
      (await saved()).columns,
      [
        { ...uniqueColumns[0], feed: { kind: "source", id: catalog.sources[0].id } },
        ...uniqueColumns.slice(1),
      ],
      "Legacy duplicate selections keep the first stable identity and save normalized settings",
    );
    assert.deepEqual(
      account.columns,
      preservedLatest,
      "My feed changes leave legacy Latest data intact",
    );
    while (await columns().count()) {
      const before = await identities();
      await columnAction(page, columns().first(), /^Remove column: /);
      await waitOrder(before.slice(1));
      if (before.length > 1)
        await page.waitForFunction(
          (id) => document.activeElement?.getAttribute("data-column-id") === id,
          before[1],
        );
    }
    const addButton = workspace().getByRole("button", { name: "Add column", exact: true });
    await page.waitForFunction(() => document.activeElement?.textContent?.trim() === "Add column");
    assert.equal(await addButton.isEnabled(), true);
    await workspace().getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await page.reload();
    await workspace().getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await waitOrder([]);
    menuRemoval.emptyAfterReload = true;
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        {
          signedIn,
          cryptoCapabilities,
          nativeScrolls,
          routeScopes,
          originalIds,
          configured,
          saved: await saved(),
          accountWrites: writes.length,
          duplicateProtection: { create: true, edit: true, legacyKeepFirst: true },
          menuRemoval,
        },
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
    if (touchSession) {
      await touchSession
        .send("Emulation.setTouchEmulationEnabled", { enabled: false })
        .catch(() => {});
      await touchSession.detach().catch(() => {});
    }
    page.off("request", recordFeedRead);
    await context.unroute(accountPattern, accountHandler);
    await context.unroute(catalogPattern, catalogHandler);
    await scopedViews.cleanup();
  }
}

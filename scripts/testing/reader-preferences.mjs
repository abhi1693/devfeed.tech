import assert from "node:assert/strict";
import { chooseArticleView, openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";

const guestKey = "devfeed:reader-preferences:guest";
const legacyKey = "devfeed:feed-view:guest";
const existingAccount = "11111111-1111-4111-8111-111111111111";
const newAccount = "99999999-9999-4999-8999-999999999999";
const columns = [
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", feed: { kind: "latest" } },
  { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2", feed: { kind: "recommended" } },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
    feed: { kind: "source", id: "33333333-3333-4333-8333-333333333333" },
  },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4",
    feed: { kind: "topic", id: "55555555-5555-4555-8555-555555555555" },
  },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5",
    feed: { kind: "tag", id: "66666666-6666-4666-8666-666666666666" },
  },
];
const accountColumns = [columns[4], columns[0], columns[2]];

/** Exercise account reconciliation through the real reader provider and Appearance controls. */
export async function checkReaderPreferences(page, target, { session, csrfToken }) {
  const context = page.context();
  let owner = null;
  const accounts = new Map([
    [existingAccount, { view: "cards", columns: structuredClone(accountColumns) }],
    [newAccount, { view: "cards", columns: [] }],
  ]);
  for (const settings of accounts.values()) {
    settings.languages = ["en"];
    settings.content_types = ["news", "article"];
  }
  const reads = [];
  const writes = [];
  const pattern = /\/api\/v1\/user\/(?:auth\/me|settings\/feed)(?:\?|$)/;
  const handle = async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname.endsWith("/auth/me"))
      return route.fulfill({
        json: owner
          ? {
              user_id: owner,
              name: "Reader",
              email: "reader@example.test",
              csrf_token: csrfToken,
              expires_at: Math.floor(Date.now() / 1000) + 3600,
            }
          : null,
      });
    if (!owner) return route.fulfill({ status: 401, json: {} });
    if (request.method() === "PUT") {
      const payload = request.postDataJSON();
      assert.equal((await request.allHeaders())["x-csrf-token"], csrfToken);
      writes.push({ owner, payload });
      accounts.set(owner, structuredClone(payload));
    } else {
      assert.equal(request.method(), "GET");
      reads.push(owner);
    }
    return route.fulfill({ json: accounts.get(owner) });
  };
  const guestWorkspace = { version: 1, view: "compact", columns: structuredClone(columns) };
  const storedGuest = () => page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey);
  const assertView = async (currentPage, name, message) => {
    const region = await openArticleViewSettings(currentPage);
    assert.equal(await region.getByRole("radio", { name, exact: true }).isChecked(), true, message);
  };
  const changeAccount = async (nextOwner) => {
    owner = nextOwner;
    await session(nextOwner);
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
  };
  await context.route(pattern, handle);
  try {
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    await openArticleViewSettings(page);
    await page.evaluate(
      ({ guestKey, legacyKey }) => {
        localStorage.removeItem(guestKey);
        localStorage.setItem(legacyKey, "compact");
      },
      { guestKey, legacyKey },
    );
    await page.reload();
    await assertView(page, "List", "A legacy guest layout survives the storage upgrade");
    await chooseArticleView(page, "cards");
    assert.deepEqual(await storedGuest(), { version: 1, view: "cards", columns: [] });

    await page.evaluate(
      ({ guestKey, legacyKey, workspace }) => {
        localStorage.setItem(guestKey, JSON.stringify(workspace));
        localStorage.setItem(legacyKey, "cards");
      },
      { guestKey, legacyKey, workspace: guestWorkspace },
    );
    await page.reload();
    await assertView(page, "List", "Versioned guest preferences take precedence over legacy data");
    await chooseArticleView(page, "cards");
    assert.deepEqual(await storedGuest(), { ...guestWorkspace, view: "cards" });
    await chooseArticleView(page, "compact");
    await page.reload();
    await assertView(page, "List", "Guest layout and ordered columns survive a reload");
    assert.deepEqual(await storedGuest(), guestWorkspace);
    assert.deepEqual(writes, [], "Guest changes never write account preferences");

    await changeAccount(existingAccount);
    await assertView(page, "Grid", "Existing account preferences take precedence over the guest");
    assert.deepEqual(writes, [], "Signing in never replaces account settings with guest data");
    assert.deepEqual(await storedGuest(), guestWorkspace);
    await chooseArticleView(page, "compact");
    assert.deepEqual(writes.at(-1), {
      owner: existingAccount,
      payload: {
        view: "compact",
        columns: accountColumns,
        languages: ["en"],
        content_types: ["news", "article"],
      },
    });
    assert.deepEqual(
      await storedGuest(),
      guestWorkspace,
      "Account saves preserve the guest workspace",
    );
    await page.reload();
    await assertView(page, "List", "Reload restores the account's saved layout");

    const otherPage = await context.newPage();
    try {
      // Clearing the origin's storage rules out a local cache supplying account settings.
      await otherPage.goto(readerRouteUrl(target, "/settings/appearance"));
      await openArticleViewSettings(otherPage);
      await otherPage.evaluate(() => localStorage.clear());
      const readCount = reads.length;
      await otherPage.reload();
      await assertView(
        otherPage,
        "List",
        "A fresh tab restores saved account settings from the API",
      );
      assert.ok(
        reads.length > readCount,
        "The second tab fetches account settings after storage reset",
      );
      await chooseArticleView(otherPage, "cards");
      assert.deepEqual(writes.at(-1).payload.columns, accountColumns);
      assert.equal(
        await otherPage.evaluate((key) => localStorage.getItem(key), guestKey),
        null,
        "Account settings are never copied into guest storage",
      );
    } finally {
      await otherPage.close();
    }
    await page.bringToFront();
    await page.reload();
    await assertView(page, "Grid", "Another tab's API save is restored on the next load");
    await page.evaluate(
      ({ key, workspace }) => localStorage.setItem(key, JSON.stringify(workspace)),
      { key: guestKey, workspace: guestWorkspace },
    );
    await changeAccount(null);
    await assertView(page, "List", "Signing out restores the original guest layout");
    assert.deepEqual(await storedGuest(), guestWorkspace);

    const beforeNewAccount = writes.length;
    await changeAccount(newAccount);
    await assertView(
      page,
      "Grid",
      "A new account's default layout also takes precedence over guest data",
    );
    assert.equal(
      writes.length,
      beforeNewAccount,
      "A new account is not seeded from guest preferences",
    );
    await chooseArticleView(page, "compact");
    assert.deepEqual(
      writes.at(-1).payload.columns,
      [],
      "The new account has its own empty workspace",
    );
    await changeAccount(existingAccount);
    await assertView(page, "Grid", "Switching accounts never leaks the previous account's layout");
    await chooseArticleView(page, "compact");
    assert.deepEqual(writes.at(-1).payload.columns, accountColumns);
    await changeAccount(newAccount);
    await assertView(page, "List", "Each account keeps its own saved layout");
    await chooseArticleView(page, "cards");
    assert.deepEqual(
      writes.at(-1).payload.columns,
      [],
      "Column configurations stay scoped to their account",
    );
    assert.deepEqual(
      await storedGuest(),
      guestWorkspace,
      "Account switches leave guest preferences intact",
    );
    await changeAccount(null);
    await assertView(
      page,
      "List",
      "Signing out of either account restores the same guest workspace",
    );
    assert.deepEqual(await storedGuest(), guestWorkspace);

    for (const savedView of ["stacked", "columns"]) {
      const renderedView = savedView === "stacked" ? "Stacked" : "Grid";
      accounts.set(existingAccount, {
        view: savedView,
        columns: structuredClone(accountColumns),
        languages: ["en"],
        content_types: ["news", "article"],
      });
      await changeAccount(existingAccount);
      await assertView(
        page,
        renderedView,
        `The saved ${savedView} layout uses its available renderer`,
      );
      await page.goto(target);
      await page
        .locator(savedView === "stacked" ? ".article-stacked-row:visible" : ".article-card:visible")
        .first()
        .waitFor();
      await page.goto(readerRouteUrl(target, "/settings/feed"));
      const feed = page.getByRole("region", { name: "Feed preferences", exact: true });
      await feed.waitFor();
      await page.getByRole("searchbox", { name: "Find a language", exact: true }).fill("French");
      await page.getByRole("checkbox", { name: "French", exact: true }).check();
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await page.getByText("Feed settings saved.", { exact: true }).waitFor();
      assert.deepEqual(writes.at(-1).payload, {
        view: savedView,
        columns: accountColumns,
        languages: ["en", "fr"],
        content_types: ["news", "article"],
      });
      await page.reload();
      const french = page.getByRole("checkbox", { name: "French", exact: true });
      await french.waitFor();
      assert.equal(await french.isChecked(), true);
      await assertView(
        page,
        renderedView,
        "Saving languages retains the stored layout and its available renderer",
      );
      await chooseArticleView(page, "compact");
      await chooseArticleView(page, "cards");
      assert.equal(writes.at(-1).payload.view, "cards", "Selecting Grid saves the explicit layout");
      assert.deepEqual(writes.at(-1).payload.columns, accountColumns);
      assert.deepEqual(await storedGuest(), guestWorkspace);
      await changeAccount(null);
      await assertView(
        page,
        "List",
        "Unavailable account layouts never replace the guest's layout",
      );
    }
  } finally {
    await context.unroute(pattern, handle);
  }
}

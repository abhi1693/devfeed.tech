import assert from "node:assert/strict";
import { chooseArticleView, openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";
import { createFixtureFeedViews, installFixtureFeedViews } from "./feed-view-fixture.mjs";

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

/** Account layouts use private scoped receipts while legacy guest/workspace data stays intact. */
export async function checkReaderPreferences(page, target, { session, csrfToken }) {
  const context = page.context();
  const guestViewsKey = "devfeed:feed-views:guest";
  let owner = null;
  const accounts = new Map([
    [
      existingAccount,
      {
        view: "cards",
        columns: structuredClone(accountColumns),
        languages: ["en"],
        content_types: ["news", "article"],
      },
    ],
    [
      newAccount,
      { view: "cards", columns: [], languages: ["en"], content_types: ["news", "article"] },
    ],
  ]);
  const stores = new Map();
  const storeFor = (id) => {
    if (!stores.has(id))
      stores.set(
        id,
        createFixtureFeedViews(() => accounts.get(id).view),
      );
    return stores.get(id);
  };
  const reads = [];
  const writes = [];
  const layoutWrites = [];
  const scoped = await installFixtureFeedViews(page, {
    store: () => storeFor(owner),
    isSignedIn: () => owner !== null,
    onSave: (record) => layoutWrites.push({ owner, ...record }),
  });
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
      accounts.set(owner, { ...accounts.get(owner), ...structuredClone(payload) });
    } else {
      assert.equal(request.method(), "GET");
      reads.push(owner);
    }
    return route.fulfill({ json: accounts.get(owner) });
  };
  const guestWorkspace = { version: 1, view: "compact", columns: structuredClone(columns) };
  const storedGuest = () => page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey);
  const storedViews = () =>
    page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestViewsKey);
  const assertView = async (currentPage, name, message) => {
    const region = await openArticleViewSettings(currentPage, {
      feed: owner ? "my_feed" : "latest",
    });
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
      ({ guestKey, legacyKey, guestViewsKey }) => {
        localStorage.removeItem(guestKey);
        localStorage.removeItem(guestViewsKey);
        localStorage.setItem(legacyKey, "compact");
      },
      { guestKey, legacyKey, guestViewsKey },
    );
    await page.reload();
    await assertView(page, "List", "Legacy guest layout remains the fallback before a scoped save");
    await chooseArticleView(page, "cards");
    assert.equal(
      await storedGuest(),
      null,
      "Scoped saves never create or overwrite the legacy workspace record",
    );
    assert.deepEqual(await storedViews(), {
      version: 1,
      default_view: "compact",
      views: { latest: "cards" },
    });
    assert.equal(await page.evaluate((key) => localStorage.getItem(key), legacyKey), "compact");

    await page.evaluate(
      ({ guestKey, legacyKey, guestViewsKey, workspace }) => {
        localStorage.setItem(guestKey, JSON.stringify(workspace));
        localStorage.setItem(legacyKey, "cards");
        localStorage.removeItem(guestViewsKey);
      },
      { guestKey, legacyKey, guestViewsKey, workspace: guestWorkspace },
    );
    await page.reload();
    await assertView(
      page,
      "List",
      "Versioned guest preferences take precedence over the old layout key",
    );
    await chooseArticleView(page, "cards");
    assert.deepEqual(await storedGuest(), guestWorkspace);
    assert.equal((await storedViews()).default_view, "compact");
    await chooseArticleView(page, "compact");
    await page.reload();
    await assertView(page, "List", "Scoped guest layout survives reload");
    const guestLayouts = await storedViews();
    assert.deepEqual(await storedGuest(), guestWorkspace);
    assert.deepEqual(layoutWrites, []);
    assert.deepEqual(writes, []);

    await changeAccount(existingAccount);
    await assertView(page, "Grid", "Private account layout takes precedence over the guest");
    assert.deepEqual(layoutWrites, [], "Signing in never imports guest layouts");
    await chooseArticleView(page, "compact");
    assert.deepEqual(layoutWrites.at(-1), {
      owner: existingAccount,
      feed: "my_feed",
      payload: { view: "compact" },
    });
    assert.deepEqual(writes, [], "Layout changes never PUT legacy feed preferences");
    assert.equal(accounts.get(existingAccount).view, "cards");
    assert.deepEqual(accounts.get(existingAccount).columns, accountColumns);
    assert.deepEqual(await storedGuest(), guestWorkspace);
    assert.deepEqual(await storedViews(), guestLayouts);
    await page.reload();
    await assertView(page, "List", "Reload restores the account's scoped API layout");

    const otherPage = await context.newPage();
    try {
      await otherPage.goto(readerRouteUrl(target, "/settings/appearance"));
      await openArticleViewSettings(otherPage);
      await otherPage.evaluate(() => localStorage.clear());
      const previous = scoped.reads.length;
      await otherPage.reload();
      await assertView(
        otherPage,
        "List",
        "A fresh tab gets the account layout from its private API",
      );
      assert.ok(scoped.reads.length > previous);
      await chooseArticleView(otherPage, "cards");
      assert.deepEqual(accounts.get(existingAccount).columns, accountColumns);
      assert.equal(await otherPage.evaluate((key) => localStorage.getItem(key), guestKey), null);
      assert.equal(
        await otherPage.evaluate((key) => localStorage.getItem(key), guestViewsKey),
        null,
      );
    } finally {
      await otherPage.close();
    }
    await page.bringToFront();
    await page.reload();
    await assertView(page, "Grid", "Another tab's scoped API save is restored");
    await page.evaluate(
      ({ guestKey, guestViewsKey, workspace, layouts }) => {
        localStorage.setItem(guestKey, JSON.stringify(workspace));
        localStorage.setItem(guestViewsKey, JSON.stringify(layouts));
      },
      { guestKey, guestViewsKey, workspace: guestWorkspace, layouts: guestLayouts },
    );
    await changeAccount(null);
    await assertView(page, "List", "Signing out restores guest layouts");
    assert.deepEqual(await storedGuest(), guestWorkspace);
    assert.deepEqual(await storedViews(), guestLayouts);

    const beforeNew = layoutWrites.length;
    await changeAccount(newAccount);
    await assertView(page, "Grid", "A new account starts from its own default");
    assert.equal(layoutWrites.length, beforeNew);
    await chooseArticleView(page, "compact");
    assert.deepEqual(accounts.get(newAccount).columns, []);
    await changeAccount(existingAccount);
    await assertView(page, "Grid", "Switching accounts cannot leak layouts");
    await chooseArticleView(page, "compact");
    await changeAccount(newAccount);
    await assertView(page, "List", "Each account retains its own My feed layout");
    await chooseArticleView(page, "cards");
    assert.deepEqual(accounts.get(existingAccount).columns, accountColumns);
    assert.deepEqual(accounts.get(newAccount).columns, []);
    await changeAccount(null);
    await assertView(page, "List", "Guest choices survive both account sessions");

    for (const savedView of ["stacked", "columns"]) {
      accounts.set(existingAccount, {
        view: savedView,
        columns: structuredClone(accountColumns),
        languages: ["en"],
        content_types: ["news", "article"],
      });
      storeFor(existingAccount).reset();
      await changeAccount(existingAccount);
      const rendered = savedView === "stacked" ? "Stacked" : "Columns";
      await assertView(page, rendered, "Unmigrated private layouts retain their legacy fallback");
      await page.goto(readerRouteUrl(target, "/"));
      if (savedView === "columns")
        await page.getByRole("region", { name: "Columns workspace", exact: true }).waitFor();
      else await page.locator(".article-stacked-row:visible").first().waitFor();
      await page.goto(readerRouteUrl(target, "/settings/feed"));
      await page.getByRole("searchbox", { name: "Find a language", exact: true }).fill("French");
      await page.getByRole("checkbox", { name: "French", exact: true }).check();
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await page.getByText("Feed settings saved.", { exact: true }).waitFor();
      assert.equal(
        accounts.get(existingAccount).view,
        savedView,
        "Filter saves preserve the legacy fallback",
      );
      assert.deepEqual(accounts.get(existingAccount).languages, ["en", "fr"]);
      assert.deepEqual(accounts.get(existingAccount).columns, accountColumns);
      await assertView(page, rendered, "Changing languages keeps the current feed layout");
      const globalWrites = writes.length;
      await chooseArticleView(page, "compact");
      await chooseArticleView(page, "cards");
      assert.equal(layoutWrites.at(-1).payload.view, "cards");
      assert.equal(writes.length, globalWrites, "Scoped layout updates do not save global filters");
      assert.equal(accounts.get(existingAccount).view, savedView);
      assert.deepEqual(await storedGuest(), guestWorkspace);
      assert.deepEqual(await storedViews(), guestLayouts);
      await changeAccount(null);
      await assertView(page, "List", "Private layout changes never replace guest layouts");
    }
  } finally {
    await context.unroute(pattern, handle);
    await scoped.cleanup();
  }
}

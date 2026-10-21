import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { chooseInlineArticleView, readerRouteUrl } from "./article-view.mjs";
import { columnAction, columnsCatalog } from "./columns-workspace.mjs";
import {
  columnsFeedSelections,
  columnsSharedArticle,
  installColumnsFeedFixture,
} from "./columns-feed-fixture.mjs";
import { createFixtureFeedViews } from "./feed-view-fixture.mjs";
import { writeReaderViewReport } from "./reader-views-evidence.mjs";

const accountA = "11111111-1111-4111-8111-111111111111";
const accountB = "99999999-9999-4999-8999-999999999999";
const guestKey = "devfeed:reader-preferences:guest";
const viewsKey = "devfeed:feed-views:guest";
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const wait = async (predicate, message) => {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) {
    assert.ok(Date.now() < deadline, message);
    await new Promise((resolve) => setTimeout(resolve, 30));
  }
};

/** Compose guest scopes, real workspace DOM ownership, and stale private transport isolation. */
export async function checkSavedReaderLifecycle(page, target, output, { session, csrfToken } = {}) {
  assert.equal(typeof session, "function");
  assert.ok(csrfToken);
  const context = page.context();
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  let owner = null;
  const expiresAt = Math.floor(Date.now() / 1000) + 3600;
  const accounts = new Map([
    [
      accountA,
      {
        views: createFixtureFeedViews(),
        columns: structuredClone([
          columnsFeedSelections[4],
          columnsFeedSelections[2],
          columnsFeedSelections[1],
        ]),
      },
    ],
    [accountB, { views: createFixtureFeedViews(), columns: [] }],
  ]);
  accounts.get(accountB).views.write("my_feed", { view: "columns" });
  const requests = [];
  const writes = [];
  const stages = [];
  let authGate = null;
  let pageGate = null;
  const statePattern =
    /\/api\/v1\/user\/(?:auth\/me|settings\/(?:profile|feed|feed-view|columns(?:\/starters)?))(?:\?|$)/;
  const stateHandler = async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const atStart = owner;
    if (url.pathname.endsWith("/auth/me")) {
      const gate = authGate;
      if (gate) {
        gate.started.resolve();
        await gate.released.promise;
      }
      return route.fulfill({
        json: atStart
          ? {
              user_id: atStart,
              name: atStart === accountA ? "Account A" : "Account B",
              email: null,
              csrf_token: csrfToken,
              expires_at: expiresAt,
            }
          : null,
      });
    }
    requests.push({
      owner: atStart,
      endpoint: url.pathname.replace("/api/v1/user/", ""),
      method: request.method(),
    });
    if (!atStart) return route.fulfill({ status: 401, json: {} });
    const account = accounts.get(atStart);
    if (url.pathname.endsWith("/settings/profile"))
      return route.fulfill({
        json: {
          display_name: atStart === accountA ? "Account A" : "Account B",
          username: null,
          avatar_url: null,
        },
      });
    if (url.pathname.endsWith("/settings/feed-view")) {
      const feed = url.searchParams.get("feed");
      let value;
      if (request.method() === "PUT") {
        assert.ok(
          (await request.allHeaders())["x-csrf-token"] === csrfToken,
          "Private writes retain CSRF protection",
        );
        const payload = request.postDataJSON();
        value = account.views.write(feed, payload);
        writes.push({ owner: atStart, kind: "layout", feed, view: payload.view });
      } else value = account.views.read(feed);
      assert.ok(value);
      return route.fulfill({ json: value });
    }
    if (url.pathname.endsWith("/columns/starters")) return route.fulfill({ json: { feeds: [] } });
    if (url.pathname.endsWith("/columns")) {
      const scope = url.searchParams.get("workspace") ?? "latest";
      if (scope !== "my_feed") return route.fulfill({ json: { initialized: true, columns: [] } });
      if (request.method() === "PUT") {
        assert.ok(
          (await request.allHeaders())["x-csrf-token"] === csrfToken,
          "Private writes retain CSRF protection",
        );
        const payload = request.postDataJSON();
        assert.equal(payload.mode, "replace");
        account.columns = structuredClone(payload.columns);
        writes.push({ owner: atStart, kind: "columns", columns: structuredClone(account.columns) });
      }
      return route.fulfill({ json: { initialized: true, columns: account.columns } });
    }
    assert.equal(request.method(), "GET", "Scoped changes never rewrite legacy global settings");
    return route.fulfill({
      json: { view: "cards", columns: [], languages: ["en"], content_types: ["article", "news"] },
    });
  };
  const privateFeedPattern = /\/api\/v1\/user\/feed(?:\?|$)/;
  const privateFeedHandler = async (route) => {
    const atStart = owner;
    const url = new URL(route.request().url());
    requests.push({
      owner: atStart,
      endpoint: "feed",
      cursor: url.searchParams.get("cursor") ?? "",
      method: "GET",
    });
    if (!atStart) return route.fulfill({ status: 401, json: {} });
    const next = !!url.searchParams.get("cursor");
    const gate = next && atStart === accountA ? pageGate : null;
    const letter = atStart === accountA ? "A" : "B";
    const items = Array.from({ length: 4 }, (_, index) => ({
      ...columnsSharedArticle,
      id: `eeeeeeee-eeee-4eee-8eee-${String((letter === "A" ? 100 : 200) + (next ? 4 : 0) + index).padStart(12, "0")}`,
      slug: `lifecycle-${letter.toLowerCase()}-${next ? 1 : 0}-${index}`,
      title: `${gate ? "Late account A" : `Account ${letter}`} recommendation ${index + 1}`,
    }));
    if (gate) {
      gate.started.resolve();
      await gate.released.promise;
    }
    try {
      await route.fulfill({
        json: {
          items,
          next_cursor: next ? null : "personal-next",
          status: "ready",
          has_interests: true,
          generation: `lifecycle-${letter}`,
          reasons: {},
        },
      });
      if (gate) gate.delivery = "completed";
    } catch {
      if (gate) gate.delivery = "aborted";
    } finally {
      if (gate) gate.finished = true;
    }
  };
  await context.route(statePattern, stateHandler);
  await context.route(privateFeedPattern, privateFeedHandler);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const ids = () => columns.evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  const definitions = async (expected) => {
    await workspace.waitFor();
    await wait(
      async () => JSON.stringify(await ids()) === JSON.stringify(expected.map(({ id }) => id)),
      "Workspace displays only the current owner's saved identities",
    );
  };
  const layout = async (view) => {
    const label = { cards: "Grid", compact: "List", stacked: "Stacked", columns: "Columns" }[view];
    await page.waitForFunction(
      (label) =>
        [...document.querySelectorAll('button[aria-label^="Change feed layout"]')].some(
          (button) => !button.disabled && button.title === `Layout: ${label}`,
        ),
      label,
    );
  };
  const home = readerRouteUrl(target, "/");
  const latest = readerRouteUrl(target, "/latest");
  const trending = readerRouteUrl(target, "/trending");
  const switchOwner = async (next, { hold = false } = {}) => {
    owner = next;
    fixture.setMember(!!next);
    if (hold) authGate = { started: deferred(), released: deferred() };
    await session(next);
    await page.evaluate(() => window.dispatchEvent(new PopStateEvent("popstate")));
    if (hold) await authGate.started.promise;
  };
  const proof = { stages, writes, requests, staleResponse: null };
  await mkdir(output, { recursive: true });
  await writeReaderViewReport(output, { status: "running", ...proof });
  try {
    await session(null);
    await page.goto(latest);
    const guest = {
      version: 1,
      view: "cards",
      columns_initialized: true,
      columns: [{ id: "f0000000-0000-4000-8000-000000000001", feed: { kind: "latest" } }],
    };
    await page.evaluate(
      ({ guestKey, viewsKey, guest }) => {
        localStorage.setItem(guestKey, JSON.stringify(guest));
        localStorage.removeItem(viewsKey);
      },
      { guestKey, viewsKey, guest },
    );
    await page.reload();
    await chooseInlineArticleView(page, "stacked", { keyboard: true });
    await page.goto(trending);
    await chooseInlineArticleView(page, "compact", { keyboard: true });
    const guestViews = await page.evaluate((key) => localStorage.getItem(key), viewsKey);
    assert.equal(
      requests.filter(({ owner }) => !owner).length,
      0,
      "Guest layouts make no private settings or feed calls",
    );
    stages.push({ stage: "guest", latest: "stacked", trending: "compact" });

    await switchOwner(accountA);
    await page.goto(home);
    await layout("cards");
    await chooseInlineArticleView(page, "columns", { keyboard: true });
    await definitions(accounts.get(accountA).columns);
    const source = workspace.locator(`[data-column-id="${columnsFeedSelections[1].id}"]`);
    await columnAction(page, source, `Move left: ${columnsCatalog.sources[0].name} column`, {
      keyboard: true,
    });
    const savedA = structuredClone(accounts.get(accountA).columns);
    await definitions(savedA);
    await page.goto(latest);
    await chooseInlineArticleView(page, "stacked", { keyboard: true });
    await page.goto(home);
    await definitions(savedA);
    const recommended = workspace.locator(`[data-column-id="${columnsFeedSelections[4].id}"]`);
    await recommended
      .getByRole("link", { name: "Account A recommendation 1", exact: true })
      .waitFor();
    const privateRow = await recommended
      .locator("article[data-article-id]")
      .first()
      .elementHandle();
    pageGate = { started: deferred(), released: deferred(), finished: false };
    await recommended.getByRole("button", { name: "Load more articles", exact: true }).click();
    await pageGate.started.promise;
    stages.push({
      stage: "account-a",
      columns: savedA.map(({ id }) => id),
      latest: "stacked",
      my_feed: "columns",
    });
    await page.screenshot({ path: join(output, "account-a.png"), animations: "disabled" });

    await switchOwner(accountB, { hold: true });
    await wait(
      async () => !(await privateRow.isVisible()),
      "A recheck immediately masks the old private workspace",
    );
    authGate.released.resolve();
    authGate = null;
    await layout("columns");
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await definitions([]);
    pageGate.released.resolve();
    await wait(() => pageGate.finished, "The old account's delayed feed response settles");
    assert.equal(await page.getByText(/Late account A recommendation/).count(), 0);
    assert.equal(await page.getByText(/Account A recommendation/).count(), 0);
    assert.equal(await privateRow.evaluate((node) => node.isConnected), false);
    await privateRow.dispose();
    assert.equal(
      writes.filter(({ owner }) => owner === accountB).length,
      0,
      "A stale feed cannot write B's preferences",
    );
    await page.reload();
    await definitions([]);
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    assert.equal(
      requests.filter(({ endpoint }) => endpoint === "settings/columns/starters").length,
      0,
      "An intentionally empty account is never reseeded",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: join(output, "account-b-empty-mobile.png"),
      animations: "disabled",
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(latest);
    await layout("cards");
    await chooseInlineArticleView(page, "compact", { keyboard: true });
    stages.push({ stage: "account-b", columns: [], latest: "compact", my_feed: "columns" });

    await switchOwner(accountA);
    await layout("stacked");
    await page.goto(home);
    await definitions(savedA);
    await recommended
      .getByRole("link", { name: "Account A recommendation 1", exact: true })
      .waitFor();
    await page.reload();
    await definitions(savedA);
    assert.equal(accounts.get(accountA).views.read("latest").view, "stacked");
    assert.equal(accounts.get(accountB).views.read("latest").view, "compact");
    assert.deepEqual(accounts.get(accountB).columns, []);
    stages.push({ stage: "account-a-restored", columns: await ids() });
    proof.staleResponse = {
      maskedDuringCheck: true,
      oldDOMDetached: true,
      delivery: pageGate.delivery,
      accountBColumnsUnchanged: true,
    };

    await switchOwner(null);
    await page.getByRole("banner").getByRole("link", { name: "Sign in", exact: true }).waitFor();
    assert.equal(await columns.count(), 0);
    await page.goto(latest);
    await layout("stacked");
    await page.goto(trending);
    await layout("compact");
    await page.goto(home);
    await layout("stacked");
    assert.equal(await workspace.count(), 0);
    assert.deepEqual(
      await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), guestKey),
      guest,
    );
    assert.equal(await page.evaluate((key) => localStorage.getItem(key), viewsKey), guestViews);
    assert.equal(
      requests.filter(({ owner }) => !owner).length,
      0,
      "Sign-out stops new private workspace reads",
    );
    stages.push({ stage: "guest-restored", latest: "stacked", trending: "compact" });
    await writeReaderViewReport(output, { status: "passed", ...proof });
  } catch (error) {
    await page
      .screenshot({ path: join(output, "failure.png"), animations: "disabled" })
      .catch(() => {});
    await writeReaderViewReport(output, {
      status: "failed",
      ...proof,
      failure: String(error.message),
    });
    throw error;
  } finally {
    authGate?.released.resolve();
    pageGate?.released.resolve();
    await context.unroute(statePattern, stateHandler);
    await context.unroute(privateFeedPattern, privateFeedHandler);
    await fixture.cleanup();
  }
}

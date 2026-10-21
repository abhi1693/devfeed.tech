import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { readerRouteUrl, openArticleViewSettings } from "./article-view.mjs";
import { columnAction, columnsCatalog } from "./columns-workspace.mjs";
import {
  installPersonalColumnsFixture,
  personalStarterFeeds,
} from "./columns-personal-fixture.mjs";

const guestKey = "devfeed:reader-preferences:guest";
const guestColumns = [
  { id: "f0000000-0000-4000-8000-000000000001", feed: { kind: "latest" } },
  {
    id: "f0000000-0000-4000-8000-000000000002",
    feed: { kind: "topic", id: columnsCatalog.topics[0].id },
  },
  {
    id: "f0000000-0000-4000-8000-000000000003",
    feed: { kind: "topic", id: columnsCatalog.topics[1].id },
  },
];
const wait = async (predicate, message) => {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) {
    assert.ok(Date.now() < deadline, message);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
};

/** Personalized starters and user changes are visible only inside signed-in My feed. */
export async function checkPersonalColumns(
  page,
  target,
  output,
  { signedIn = false, expireSession } = {},
) {
  assert.equal(signedIn, true, "Personal Columns are available only to authenticated readers");
  const state = await installPersonalColumnsFixture(page, { signedIn });
  state.setLegacy({ initialized: true, columns: guestColumns });
  state.setPersonal({ initialized: false, columns: [] });
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const home = readerRouteUrl(target, "/");
  const latestUrl = readerRouteUrl(target, "/latest");
  const appearance = readerRouteUrl(target, "/settings/appearance");
  const ids = () => columns.evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  const waitDefinitions = async (expected) => {
    await workspace.waitFor();
    await wait(
      async () => JSON.stringify(await ids()) === JSON.stringify(expected.map(({ id }) => id)),
      "The selected workspace displays only its own saved columns",
    );
  };
  const waitCount = async (count) =>
    wait(
      async () => (await columns.count()) === count,
      `Expected ${count} columns in this workspace`,
    );
  const removeAll = async () => {
    while (await columns.count()) {
      const before = await columns.count();
      await columnAction(page, columns.first(), /^Remove column: /);
      await waitCount(before - 1);
    }
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
  };
  const personalReads = () => state.requests.filter(({ kind }) => kind === "personal-starters");
  const stagePersonal = async (value, feeds = personalStarterFeeds) => {
    await page.goto(appearance);
    state.setPersonal(value);
    state.setFeeds(feeds);
    await page.reload();
    await openArticleViewSettings(page);
  };
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(appearance);
    await page.evaluate(
      ({ key, columns, signedIn }) => {
        if (!signedIn || localStorage.getItem(key) === null)
          localStorage.setItem(
            key,
            JSON.stringify({ version: 1, view: "columns", columns, columns_initialized: true }),
          );
      },
      { key: guestKey, columns: guestColumns, signedIn },
    );
    const guestBefore = await page.evaluate((key) => localStorage.getItem(key), guestKey);
    await page.reload();
    await page.goto(latestUrl);
    const latest = state.latest;
    const normalFeed = async () => {
      await page.locator(".article-card:visible").first().waitFor();
      assert.equal(await workspace.count(), 0, "Only My feed displays Columns");
    };
    await normalFeed();
    assert.deepEqual(latest, { initialized: true, columns: guestColumns });
    assert.equal(personalReads().length, 0, "Latest never prepares a personal workspace");
    await page.goto(home);
    await waitCount(5);
    const personal = state.personal.columns;
    assert.deepEqual(
      personal.map(({ feed }) => feed),
      [{ kind: "recommended" }, ...personalStarterFeeds],
    );
    assert.equal(
      new Set([...latest.columns, ...personal].map(({ id }) => id)).size,
      latest.columns.length + personal.length,
      "My feed owns independent column identities",
    );
    assert.equal(personalReads().length, 1);
    assert.deepEqual(state.latest, latest);
    assert.equal(
      state.fixture.metadata.filter(
        ({ key, url }) => !key && new URL(url).searchParams.get("sort") === "articles",
      ).length,
      0,
      "My feed uses account selections without fetching popular topics",
    );
    for (const { id } of personal)
      await workspace.locator(`[data-column-id="${id}"] .article-stacked-row`).first().waitFor();
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 1000 });
    await columns
      .nth(1)
      .evaluate((node) =>
        node.scrollIntoView({ block: "nearest", inline: "start", behavior: "instant" }),
      );
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: "instant" }));
    await page.screenshot({ path: `${output}-mobile.png`, animations: "disabled" });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.setViewportSize({ width: 1440, height: 1000 });

    const first = workspace.locator(`[data-column-id="${personal[0].id}"]`);
    await columnAction(page, first, "Change feed for Recommended column");
    const edit = page.getByRole("dialog", { name: "Edit column", exact: true });
    await edit.getByRole("combobox", { name: "Feed type", exact: true }).selectOption("latest");
    await edit.getByRole("button", { name: "Save column", exact: true }).click();
    await edit.waitFor({ state: "hidden" });
    await columnAction(page, first, "Move right: Latest column");
    await columnAction(
      page,
      workspace.locator(`[data-column-id="${personal[3].id}"]`),
      /^Remove column: /,
    );
    await waitCount(4);
    const custom = state.personal.columns;
    assert.equal(custom[1].id, personal[0].id);
    assert.deepEqual(custom[1].feed, { kind: "latest" });
    await page.goto(latestUrl);
    await normalFeed();
    assert.deepEqual(state.latest, latest, "My feed edits do not replace Latest");
    await page.goto(home);
    await waitDefinitions(custom);
    await page.reload();
    await waitDefinitions(custom);
    assert.equal(
      personalReads().length,
      1,
      "Returning to custom columns does not rebuild personal starters",
    );

    await removeAll();
    assert.deepEqual(state.personal, { initialized: true, columns: [] });
    await page.reload();
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await waitCount(0);
    await page.goto(latestUrl);
    await normalFeed();
    await page.goto(home);
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    assert.equal(personalReads().length, 1, "An intentionally empty My feed stays empty");
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).click();
    await waitCount(5);
    assert.deepEqual(
      state.personal.columns.map(({ feed }) => feed),
      [{ kind: "recommended" }, ...personalStarterFeeds],
    );
    assert.equal(personalReads().length, 2);
    assert.deepEqual(state.latest, latest);

    await stagePersonal({ initialized: false, columns: [] }, []);
    await page.goto(home);
    await waitCount(2);
    const fallback = state.personal.columns;
    assert.deepEqual(
      fallback.map(({ feed }) => feed),
      [{ kind: "recommended" }, { kind: "latest" }],
      "An account without eligible selections still has a Latest fallback",
    );
    assert.deepEqual(state.latest, latest);

    await page.goto(appearance);
    const legacy = {
      initialized: true,
      columns: [
        {
          id: "e0000000-0000-4000-8000-000000000001",
          feed: { kind: "source", id: columnsCatalog.sources[0].id },
        },
      ],
    };
    state.setLegacy(legacy);
    const beforeLegacy = personalReads().length;
    await page.reload();
    await page.goto(home);
    await waitDefinitions(legacy.columns);
    assert.equal(
      personalReads().length,
      beforeLegacy,
      "Legacy custom workspaces are retained without personalization",
    );
    await page.goto(appearance);
    state.setLegacy({ initialized: true, columns: [] });
    await page.reload();
    await page.goto(home);
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await waitCount(0);
    assert.equal(
      personalReads().length,
      beforeLegacy,
      "Legacy intentional empty workspaces stay empty",
    );

    // Restore the original Latest workspace before exercising pending responses.
    state.setLegacy(latest);
    await stagePersonal({ initialized: false, columns: [] });
    const scopeHold = state.holdStarters();
    await page.goto(home);
    await scopeHold.started;
    const writesBeforeScope = state.writes.length;
    await page.goto(latestUrl);
    await normalFeed();
    scopeHold.release();
    await wait(
      () => scopeHold.record()?.finished,
      "The old scope's personal selection response settles",
    );
    await page.waitForTimeout(100);
    await normalFeed();
    assert.equal(
      state.writes.length,
      writesBeforeScope,
      "Leaving My feed cancels the pending personal installation",
    );
    assert.deepEqual(state.personal, { initialized: false, columns: [] });

    await stagePersonal({ initialized: false, columns: [] });
    const accountHold = state.holdStarters();
    await page.goto(home);
    await accountHold.started;
    const writesBeforeAccount = state.writes.length;
    state.setMember(false);
    assert.equal(typeof expireSession, "function");
    await expireSession();
    await page.evaluate(() => window.dispatchEvent(new PopStateEvent("popstate")));
    await page.getByRole("banner").getByRole("link", { name: "Sign in", exact: true }).waitFor();
    const privateMasked = async () => {
      return (await ids()).length === 0;
    };
    await wait(privateMasked, "Expired credentials immediately hide the private workspace");
    accountHold.release();
    await wait(
      () => accountHold.record()?.finished,
      "The expired account's selection response settles",
    );
    await page.waitForTimeout(100);
    assert.equal(
      await privateMasked(),
      true,
      "A late response cannot expose the expired account's workspace",
    );
    assert.equal(state.writes.length, writesBeforeAccount);
    assert.deepEqual(state.latest, latest);
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), guestKey),
      guestBefore,
      "Personal workspace responses never replace guest storage",
    );
    await page.goto(latestUrl);
    await normalFeed();
    await page.goto(home);
    await normalFeed();
    assert.equal(
      state.privateRequests.filter(({ member }) => !member).length,
      0,
      "Session expiry stops new private workspace reads",
    );
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        {
          passed: true,
          signedIn,
          latest,
          personal,
          custom,
          fallback,
          legacy,
          requests: state.requests,
          writes: state.writes,
          privateRequests: state.privateRequests,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    console.error("Personal columns failure", page.url(), await page.locator("body").innerText());
    throw error;
  } finally {
    await state.cleanup();
  }
}

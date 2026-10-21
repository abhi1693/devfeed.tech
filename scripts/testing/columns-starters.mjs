import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { chooseArticleView, openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";
import { columnAction } from "./columns-workspace.mjs";
import {
  installPersonalColumnsFixture,
  personalStarterFeeds,
} from "./columns-personal-fixture.mjs";

const guestKey = "devfeed:reader-preferences:guest";
const wait = async (predicate, message) => {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) {
    assert.ok(Date.now() < deadline, message);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
};

/** My feed first-use preparation never replaces edited, cleared, or newer account columns. */
export async function checkColumnsStarters(page, target, output, { signedIn = false } = {}) {
  assert.equal(signedIn, true);
  const state = await installPersonalColumnsFixture(page, { signedIn });
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const expectedFeeds = [{ kind: "recommended" }, ...personalStarterFeeds];
  const latestBefore = state.latest;
  const reads = () => state.requests.filter(({ kind }) => kind === "personal-starters");
  const ids = () => columns.evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  const waitColumns = (count) =>
    wait(async () => (await columns.count()) === count, `Expected ${count} saved columns`);
  const assertDefinitions = async (expected) => {
    await wait(
      async () => JSON.stringify(await ids()) === JSON.stringify(expected.map(({ id }) => id)),
      "The workspace retains its saved identities and order",
    );
    assert.deepEqual(state.personal.columns, expected);
  };
  const fresh = async () => {
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    await openArticleViewSettings(page);
    state.fixture.setSettings({ view: "cards" });
    state.setPersonal({ initialized: false, columns: [] });
    await page.reload();
    const layout = await openArticleViewSettings(page);
    assert.equal(await layout.getByRole("radio", { name: "Grid", exact: true }).isChecked(), true);
  };
  const showColumns = async () => {
    await openArticleViewSettings(page);
    await chooseArticleView(page, "Columns");
    await page.goto(target, { waitUntil: "domcontentloaded" });
    await workspace.waitFor();
  };
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await fresh();
    const guestBefore = await page.evaluate((key) => localStorage.getItem(key), guestKey);
    await page.goto(target);
    await page.locator(".article-card:visible").first().waitFor();
    assert.equal(await workspace.count(), 0, "First use preserves the Grid layout");
    assert.equal(reads().length, 0, "Grid does not prepare an unused workspace");
    assert.equal(state.writes.length, 0);

    await showColumns();
    await waitColumns(expectedFeeds.length);
    const starters = state.personal.columns;
    assert.deepEqual(
      starters.map(({ feed }) => feed),
      expectedFeeds,
    );
    assert.equal(new Set(starters.map(({ id }) => id)).size, expectedFeeds.length);
    assert.equal(reads().length, 1);
    assert.equal(state.writes.length, 1);
    assert.equal(state.writes[0].scope, "my_feed");
    assert.equal(state.writes[0].mode, "initialize");
    assert.equal(
      await workspace.getByRole("combobox", { name: "Go to column", exact: true }).count(),
      0,
    );
    assert.equal(await workspace.getByText(/^\d+ of 12 columns$/).count(), 0);
    await columns.first().locator(".article-stacked-row").first().waitFor();
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
    await page.reload();
    await assertDefinitions(starters);
    await openArticleViewSettings(page);
    await chooseArticleView(page, "Grid");
    await page.goto(target);
    await page.locator(".article-card:visible").first().waitFor();
    await showColumns();
    await assertDefinitions(starters);
    assert.equal(reads().length, 1, "Returning to a saved workspace never reseeds it");

    const first = workspace.locator(`[data-column-id="${starters[0].id}"]`);
    await columnAction(page, first, "Edit Recommended column");
    const dialog = page.getByRole("dialog", { name: "Edit column", exact: true });
    await dialog.getByRole("combobox", { name: "Feed type", exact: true }).selectOption("latest");
    await dialog.getByRole("button", { name: "Save column", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
    await columnAction(page, first, "Move Latest right");
    await columnAction(
      page,
      workspace.locator(`[data-column-id="${starters.at(-1).id}"]`),
      /^Remove .+ column$/,
    );
    await waitColumns(starters.length - 1);
    const edited = state.personal.columns;
    assert.equal(edited[1].id, starters[0].id);
    assert.deepEqual(edited[1].feed, { kind: "latest" });
    await page.reload();
    await assertDefinitions(edited);
    assert.equal(reads().length, 1, "Edits and reordering preserve user choices");

    while (await columns.count()) {
      const before = await columns.count();
      await columnAction(page, columns.first(), /^Remove .+ column$/);
      await waitColumns(before - 1);
    }
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    assert.deepEqual(state.personal, { initialized: true, columns: [] });
    await page.reload();
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    await waitColumns(0);
    await openArticleViewSettings(page);
    await chooseArticleView(page, "Grid");
    await page.goto(target);
    await showColumns();
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).waitFor();
    assert.equal(reads().length, 1, "Intentionally empty My feed stays empty on return");
    await workspace.getByRole("button", { name: "Use starter columns", exact: true }).click();
    await waitColumns(expectedFeeds.length);
    assert.deepEqual(
      state.personal.columns.map(({ feed }) => feed),
      expectedFeeds,
    );
    assert.equal(state.writes.at(-1).mode, "restore_empty");
    assert.equal(reads().length, 2);

    await fresh();
    const remote = [{ id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", feed: { kind: "latest" } }];
    const race = state.holdStarters();
    await showColumns();
    await race.started;
    state.setPersonal({ initialized: true, columns: remote });
    race.release();
    await assertDefinitions(remote);
    await page.reload();
    await assertDefinitions(remote);

    await fresh();
    const preparation = state.holdStarters();
    await showColumns();
    await preparation.started;
    await workspace.getByRole("button", { name: "Add column", exact: true }).click();
    const add = page.getByRole("dialog", { name: "Add column", exact: true });
    await add.getByRole("combobox", { name: "Feed type", exact: true }).selectOption("latest");
    await add.getByRole("button", { name: "Save column", exact: true }).click();
    await add.waitFor({ state: "hidden" });
    await waitColumns(1);
    const manual = state.personal.columns;
    preparation.release();
    await wait(() => preparation.record()?.finished, "Canceled preparation settles");
    await page.waitForTimeout(100);
    await assertDefinitions(manual);
    assert.deepEqual(
      manual.map(({ feed }) => feed),
      [{ kind: "latest" }],
    );
    assert.deepEqual(
      state.latest,
      latestBefore,
      "My feed starter changes preserve legacy Latest settings",
    );
    assert.ok(state.writes.every(({ scope }) => scope === "my_feed"));
    assert.equal(await page.evaluate((key) => localStorage.getItem(key), guestKey), guestBefore);
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        {
          passed: true,
          signedIn,
          starters,
          edited,
          remote,
          manual,
          installations: state.writes,
          requests: state.requests,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    console.error("Columns starters failure", page.url(), await page.locator("body").innerText());
    throw error;
  } finally {
    await state.cleanup();
  }
}

import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "../../apps/web/tests/browser/accessibility.mjs";
import { chooseInlineArticleView, readerRouteUrl } from "./article-view.mjs";
import { columnAction, columnsCatalog } from "./columns-workspace.mjs";
import { columnsFeedSelections, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";
import { writeReaderViewReport } from "./reader-views-evidence.mjs";

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

/** A failed private workspace save keeps the reading session and can be retried with keys. */
export async function checkColumnSaveRecovery(page, target, output, { csrfToken } = {}) {
  assert.ok(csrfToken);
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  let saved = structuredClone(columnsFeedSelections.slice(0, 2));
  fixture.setColumns(saved);
  const context = page.context();
  const writes = [];
  const gate = { started: deferred(), released: deferred() };
  let rejectFirst = true;
  const pattern = /\/api\/v1\/user\/settings\/columns(?:\?|$)/;
  const appearancePattern = /\/api\/v1\/user\/settings\/appearance(?:\?|$)/;
  const appearanceHandler = (route) => route.fulfill({ json: { theme: "system" } });
  const handler = async (route) => {
    const request = route.request();
    const scope = new URL(request.url()).searchParams.get("workspace") ?? "latest";
    if (scope !== "my_feed") return route.fulfill({ json: { initialized: true, columns: [] } });
    if (request.method() === "PUT") {
      assert.ok(
        (await request.allHeaders())["x-csrf-token"] === csrfToken,
        "Private writes retain CSRF protection",
      );
      const payload = request.postDataJSON();
      assert.equal(payload.mode, "replace");
      const record = { scope, columns: structuredClone(payload.columns), status: 0 };
      writes.push(record);
      if (rejectFirst) {
        rejectFirst = false;
        gate.started.resolve();
        await gate.released.promise;
        record.status = 503;
        return route.fulfill({
          status: 503,
          json: { detail: "Workspace temporarily unavailable" },
        });
      }
      saved = structuredClone(payload.columns);
      fixture.setColumns(saved);
      record.status = 200;
    }
    return route.fulfill({ json: { initialized: true, columns: saved } });
  };
  await context.route(pattern, handler);
  await context.route(appearancePattern, appearanceHandler);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const latest = workspace.locator(`[data-column-id="${columnsFeedSelections[0].id}"]`);
  const source = workspace.locator(`[data-column-id="${columnsFeedSelections[1].id}"]`);
  const latestScroll = latest.getByRole("region", {
    name: "Articles in Latest column",
    exact: true,
  });
  const organic = ".article-stacked-row:not(.partner-placement)";
  const rows = (column) => column.locator(organic);
  const home = readerRouteUrl(target, "/");
  const latestUrl = readerRouteUrl(target, "/latest");
  const snapshots = [];
  const proof = {
    writes,
    snapshots,
    reducedMotion: null,
    preservedArticleDOM: false,
    retryFocus: null,
    failedSaveAccessibility: null,
  };
  const record = async (stage) => {
    const snapshot = {
      stage,
      ids: await columns.evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId)),
      latestRows: await rows(latest).count(),
      sourceRows: await rows(source).count(),
      latestScrollTop: await latestScroll.evaluate((node) => node.scrollTop),
      feedReads: fixture.requests.length,
    };
    snapshots.push(snapshot);
    return snapshot;
  };
  let originalRow;
  let saveElement;
  let previousTheme;
  let themeChanged = false;
  await mkdir(output, { recursive: true });
  await writeReaderViewReport(output, { status: "running", ...proof });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await page.goto(latestUrl);
    previousTheme = await page.evaluate(() => localStorage.getItem("devfeed:theme"));
    await page.evaluate(() => {
      localStorage.setItem("devfeed:theme", "system");
      window.dispatchEvent(
        new StorageEvent("storage", { key: "devfeed:theme", newValue: "system" }),
      );
    });
    themeChanged = true;
    await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
    await chooseInlineArticleView(page, "stacked", { keyboard: true });
    await page.goto(home);
    await workspace.waitFor();
    await wait(
      async () => (await rows(latest).count()) === 4 && (await rows(source).count()) === 4,
      "Two columns load their own initial articles",
    );
    const more = latest.getByRole("button", { name: "Load more articles", exact: true });
    await more.focus();
    await page.keyboard.press("Enter");
    await wait(
      async () => (await rows(latest).count()) === 7,
      "Pagination loads only Latest's next page",
    );
    await latestScroll.evaluate((node) => {
      node.scrollTop = Math.min(160, node.scrollHeight - node.clientHeight);
    });
    originalRow = await rows(latest).first().elementHandle();
    const before = await record("before-save");
    assert.ok(before.latestScrollTop > 0, "The paginated feed has a meaningful reading position");
    const assertReadingPosition = (snapshot) =>
      assert.ok(
        Math.abs(snapshot.latestScrollTop - before.latestScrollTop) <= 1,
        `${snapshot.stage} retains the independent feed's reading position`,
      );
    proof.reducedMotion = await page.evaluate(() => ({
      enabled: matchMedia("(prefers-reduced-motion: reduce)").matches,
      boardScroll: getComputedStyle(document.querySelector('[aria-label="Column feeds"]'))
        .scrollBehavior,
    }));
    assert.equal(proof.reducedMotion.enabled, true);
    assert.equal(
      proof.reducedMotion.boardScroll,
      "auto",
      "Keyboard navigation avoids animated scrolling under reduced motion",
    );
    await columnAction(page, source, `Change feed for ${columnsCatalog.sources[0].name} column`, {
      keyboard: true,
    });
    const editor = page.getByRole("dialog", { name: "Edit column", exact: true });
    const type = editor.getByRole("combobox", { name: "Feed type", exact: true });
    await type.focus();
    // Latest is disabled because it already exists; choose Topic from the enabled end.
    await page.keyboard.press("End");
    await page.keyboard.press("ArrowUp");
    assert.equal(await type.inputValue(), "topic");
    const search = editor.getByLabel("Find a topic", { exact: true });
    await search.focus();
    await page.keyboard.type(columnsCatalog.topics[0].name);
    const choice = editor.getByRole("button", { name: columnsCatalog.topics[0].name, exact: true });
    await choice.waitFor();
    await choice.focus();
    await page.keyboard.press("Enter");
    const save = editor.getByRole("button", { name: "Save column", exact: true });
    await save.focus();
    saveElement = await save.elementHandle();
    await page.keyboard.press("Enter");
    await gate.started.promise;
    const pendingSave = editor.getByRole("button", { name: "Saving…", exact: true });
    assert.equal(await pendingSave.getAttribute("aria-disabled"), "true");
    assert.equal(await pendingSave.getAttribute("aria-busy"), "true");
    assert.equal(
      await saveElement.evaluate((node) => node === document.activeElement),
      true,
      "The pending Save button retains keyboard focus",
    );
    assert.equal(await type.isDisabled(), true, "Other editor fields remain disabled during save");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(50);
    assert.equal(writes.length, 1, "Repeated Enter cannot duplicate a pending workspace mutation");
    assert.equal(await originalRow.evaluate((node) => node.isConnected), true);
    const pending = await record("pending-save");
    assert.equal(pending.latestRows, before.latestRows);
    assert.equal(pending.sourceRows, before.sourceRows);
    assertReadingPosition(pending);
    assert.equal(
      pending.feedReads,
      before.feedReads,
      "A pending settings save never reloads article pages",
    );
    gate.released.resolve();
    await editor
      .getByRole("alert")
      .getByText("Couldn’t save your columns. Please try again.", { exact: true })
      .waitFor();
    assert.equal(await save.isEnabled(), true);
    assert.equal(await originalRow.evaluate((node) => node.isConnected), true);
    const failed = await record("failed-save");
    assert.equal(failed.latestRows, 7);
    assert.equal(failed.sourceRows, 4);
    assert.equal(failed.feedReads, before.feedReads);
    assertReadingPosition(failed);
    assert.deepEqual(
      saved,
      columnsFeedSelections.slice(0, 2),
      "The rejected edit never replaces saved definitions",
    );
    proof.focusBeforeRetry = await editor.evaluate((node) => ({
      modal: node.matches(":modal"),
      inside: node.contains(document.activeElement),
      activeTag: document.activeElement?.tagName,
    }));
    proof.retryFocus = await saveElement.evaluate((node) => node === document.activeElement);
    assert.equal(proof.retryFocus, true, "A rejected save keeps focus on the same retry button");
    assert.notEqual(await save.getAttribute("aria-disabled"), "true");
    assert.notEqual(await save.getAttribute("aria-busy"), "true");
    const failedSaveTheme = await page.evaluate(() => ({
      darkClass: document.documentElement.classList.contains("dark"),
      prefersDark: matchMedia("(prefers-color-scheme: dark)").matches,
    }));
    assert.equal(failedSaveTheme.darkClass, true, "The rejected-save dialog uses the dark theme");
    assert.equal(failedSaveTheme.prefersDark, true);
    const failedSaveAxe = await new AxeBuilder({ page })
      .include("dialog[open]")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    await writeFile(
      join(output, "failed-save-axe.json"),
      `${JSON.stringify(failedSaveAxe, null, 2)}\n`,
    );
    proof.failedSaveAccessibility = {
      colorScheme: "dark",
      ...failedSaveTheme,
      violations: failedSaveAxe.violations.length,
      incomplete: failedSaveAxe.incomplete.length,
      passed: failedSaveAxe.passes.length,
    };
    assertAccessible(failedSaveAxe, "Columns failed save/dark");
    await page.screenshot({ path: join(output, "failed-save.png"), animations: "disabled" });
    await page.keyboard.press("Enter");
    await editor.waitFor({ state: "hidden" });
    await source
      .getByRole("heading", { name: columnsCatalog.topics[0].name, exact: true })
      .waitFor();
    await wait(
      async () => (await rows(source).count()) === 4,
      "The successfully edited column loads its selected topic",
    );
    assert.equal(await originalRow.evaluate((node) => node.isConnected), true);
    assert.equal(
      await rows(latest).count(),
      7,
      "A successful sibling edit retains the paginated column",
    );
    assert.equal(
      fixture.requests.filter(({ kind }) => kind === "latest").length,
      2,
      "Latest's cursor sequence never restarts after failure or retry",
    );
    assert.deepEqual(
      writes.map(({ status }) => status),
      [503, 200],
    );
    assert.deepEqual(
      writes[0].columns,
      writes[1].columns,
      "Retry keeps the same candidate identity and definition",
    );
    assert.deepEqual(
      saved.map(({ id }) => id),
      columnsFeedSelections.slice(0, 2).map(({ id }) => id),
    );
    proof.preservedArticleDOM = true;
    assertReadingPosition(await record("retry-saved"));
    await page.screenshot({ path: join(output, "retry-saved.png"), animations: "disabled" });
    await page.reload();
    await workspace.waitFor();
    await source
      .getByRole("heading", { name: columnsCatalog.topics[0].name, exact: true })
      .waitFor();
    assert.equal(await columns.count(), 2);
    assert.equal(fixture.scopedViews.store.read("my_feed").view, "columns");
    assert.equal(fixture.scopedViews.store.read("latest").view, "stacked");
    await page.goto(latestUrl);
    await page.locator(".article-stacked-row:not(.partner-placement)").first().waitFor();
    assert.equal(
      await workspace.count(),
      0,
      "The sibling public feed keeps its own Stacked layout",
    );
    assert.deepEqual(fixture.writes, [], "Workspace saves never overwrite legacy global filters");
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
    gate.released.resolve();
    await originalRow?.dispose();
    await saveElement?.dispose();
    await context.unroute(pattern, handler);
    await context.unroute(appearancePattern, appearanceHandler);
    await fixture.cleanup();
    if (themeChanged)
      await page
        .evaluate((theme) => {
          if (theme === null) localStorage.removeItem("devfeed:theme");
          else localStorage.setItem("devfeed:theme", theme);
          window.dispatchEvent(
            new StorageEvent("storage", { key: "devfeed:theme", newValue: theme }),
          );
        }, previousTheme)
        .catch(() => {});
    await page.emulateMedia({ colorScheme: null, reducedMotion: "no-preference" });
  }
}

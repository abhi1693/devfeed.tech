import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { readerRouteUrl } from "./article-view.mjs";
import { reorderColumn } from "./column-reorder.mjs";
import { columnsFeedSelections, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";
import { openColumnMenu } from "./columns-workspace.mjs";

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

/** Drag input changes only the saved order, retaining each independently loaded reading session. */
export async function checkColumnsReordering(page, target, output, { csrfToken } = {}) {
  await mkdir(dirname(output), { recursive: true });
  await Promise.all([
    rm(`${output}-failure.json`, { force: true }),
    rm(`${output}-failure.png`, { force: true }),
  ]);
  await writeFile(
    `${output}.json`,
    `${JSON.stringify({ status: "running", passed: false }, null, 2)}\n`,
  );
  assert.ok(csrfToken);
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  let saved = structuredClone([
    columnsFeedSelections[0],
    columnsFeedSelections[1],
    columnsFeedSelections[2],
    columnsFeedSelections[4],
  ]);
  fixture.setColumns(saved);
  let rejectNext = false;
  const failedSave = { started: deferred(), released: deferred() };
  const writes = [];
  const snapshots = [];
  const pointerPhases = [];
  const context = page.context();
  const pattern = /\/api\/v1\/user\/settings\/columns(?:\?|$)/;
  const handler = async (route) => {
    const request = route.request();
    assert.equal(new URL(request.url()).searchParams.get("workspace"), "my_feed");
    if (request.method() === "PUT") {
      assert.ok(
        (await request.allHeaders())["x-csrf-token"] === csrfToken,
        "Reordering retains CSRF protection",
      );
      const payload = request.postDataJSON();
      assert.equal(payload.mode, "replace");
      writes.push({ columns: structuredClone(payload.columns), status: rejectNext ? 503 : 200 });
      if (rejectNext) {
        rejectNext = false;
        failedSave.started.resolve();
        await failedSave.released.promise;
        return route.fulfill({
          status: 503,
          json: { detail: "Workspace temporarily unavailable" },
        });
      }
      saved = structuredClone(payload.columns);
      fixture.setColumns(saved);
    }
    return route.fulfill({ json: { initialized: true, columns: saved } });
  };
  await context.route(pattern, handler);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const board = workspace.getByRole("region", { name: "Column feeds", exact: true });
  const columns = workspace.locator("[data-column-id]");
  const column = (id) => workspace.locator(`[data-column-id="${id}"]`);
  const latest = column(columnsFeedSelections[0].id);
  const source = column(columnsFeedSelections[1].id);
  const organic = ".article-stacked-row:not(.partner-placement)";
  const ids = () => columns.evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  const scrolls = () =>
    columns.evaluateAll((nodes) =>
      nodes
        .map((node) => ({
          id: node.dataset.columnId,
          top: node.querySelector('[role="region"][aria-label^="Articles in "]').scrollTop,
        }))
        .sort((left, right) => left.id.localeCompare(right.id)),
    );
  const waitOrder = async (expected) =>
    wait(
      async () => JSON.stringify(await ids()) === JSON.stringify(expected),
      "The rendered order matches the acknowledged stable column identities",
    );
  let baseline;
  const prepare = async () => {
    await workspace.waitFor();
    await waitOrder(saved.map(({ id }) => id));
    await wait(
      async () => (await columns.locator(organic).count()) === 16,
      "Every initial column has four loaded articles",
    );
    for (const feed of [latest, source]) {
      await feed.getByRole("button", { name: "Load more articles", exact: true }).click();
      await wait(
        async () => (await feed.locator(organic).count()) === 7,
        "Independent columns retain their own second page",
      );
    }
    await page.evaluate(
      (ids) => {
        window.__columnReorderRows = ids.map((id) => ({
          id,
          rows: [
            ...document.querySelectorAll(
              `[data-column-id="${id}"] .article-stacked-row:not(.partner-placement)`,
            ),
          ],
        }));
      },
      saved.map(({ id }) => id),
    );
    for (const [feed, top] of [
      [latest, 160],
      [source, 80],
    ])
      await feed.getByRole("region", { name: /^Articles in .+ column$/ }).evaluate((node, top) => {
        node.scrollTop = Math.min(top, node.scrollHeight - node.clientHeight);
      }, top);
    baseline = { reads: fixture.requests.length, scrolls: await scrolls() };
    assert.ok(baseline.scrolls.filter(({ top }) => top > 0).length >= 2);
  };
  const retained = async (stage) => {
    assert.equal(
      await page.evaluate(() =>
        window.__columnReorderRows.every(({ id, rows }) =>
          rows.every(
            (row, index) =>
              row.isConnected &&
              document.querySelectorAll(
                `[data-column-id="${id}"] .article-stacked-row:not(.partner-placement)`,
              )[index] === row,
          ),
        ),
      ),
      true,
      "Dragging retains the original article DOM in every column",
    );
    assert.equal(fixture.requests.length, baseline.reads, "Dragging never reloads any feed page");
    assert.deepEqual(
      await scrolls(),
      baseline.scrolls,
      "Dragging retains independent reading positions",
    );
    snapshots.push({
      stage,
      order: await ids(),
      feedReads: fixture.requests.length,
      scrolls: await scrolls(),
      writes: writes.length,
    });
  };
  const focused = async (feed) =>
    assert.equal(
      await feed
        .locator("[data-column-drag-handle]")
        .evaluate((node) => node === document.activeElement),
      true,
      "The moved handle retains keyboard focus",
    );
  let touch;
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(readerRouteUrl(target, "/"));
    await prepare();
    const initial = saved.map(({ id }) => id);
    await reorderColumn(page, latest, 1, {
      input: "pointer",
      onPhase: async (phase) => pointerPhases.push({ phase, scrolls: await scrolls() }),
    });
    const pointerOrder = [initial[1], initial[0], ...initial.slice(2)];
    await waitOrder(pointerOrder);
    assert.equal(writes.length, 1, "A pointer drop saves once");
    await focused(latest);
    await retained("pointer-drop");
    await page.screenshot({ path: `${output}-desktop.png`, animations: "disabled" });
    await page.reload();
    await prepare();
    await waitOrder(pointerOrder);

    await reorderColumn(page, source, 1, { cancel: true });
    await waitOrder(pointerOrder);
    assert.equal(writes.length, 1, "Escape cancels the drag without saving");
    await focused(source);
    await retained("keyboard-cancel");
    await reorderColumn(page, source, 0);
    assert.equal(writes.length, 1, "Dropping on the starting slot never writes");
    await retained("keyboard-noop");
    await reorderColumn(page, source, 1);
    await waitOrder(initial);
    assert.equal(writes.length, 2, "A keyboard drop saves once");
    await focused(source);
    await retained("keyboard-drop");

    rejectNext = true;
    await reorderColumn(page, latest, 1, { input: "pointer", settle: false });
    await failedSave.started.promise;
    await waitOrder(pointerOrder);
    await focused(latest);
    assert.equal(
      await latest.locator("[data-column-drag-handle]").getAttribute("aria-disabled"),
      "true",
    );
    await retained("pending-failed-drop");
    const oldTop = await latest
      .getByRole("region", { name: "Articles in Latest column", exact: true })
      .evaluate((node) => node.scrollTop);
    await latest
      .getByRole("region", { name: "Articles in Latest column", exact: true })
      .evaluate((node) => {
        node.scrollTop += 20;
      });
    baseline = { ...baseline, scrolls: await scrolls() };
    assert.equal(
      baseline.scrolls.find(({ id }) => id === columnsFeedSelections[0].id).top,
      oldTop + 20,
      "Reading can continue while the reordered workspace is being saved",
    );
    failedSave.released.resolve();
    await page.locator('[aria-label="Columns workspace"][aria-busy="false"]').waitFor();
    await workspace
      .getByText("Couldn’t save your columns. Please try again.", { exact: true })
      .waitFor();
    await waitOrder(initial);
    assert.equal(writes.length, 3);
    assert.equal(writes.at(-1).status, 503);
    assert.deepEqual(
      saved.map(({ id }) => id),
      initial,
      "A rejected drag rolls back the saved order",
    );
    await focused(latest);
    await retained("failed-drop");
    await reorderColumn(page, latest, 1);
    await waitOrder(pointerOrder);
    assert.equal(writes.length, 4);
    await retained("retried-drop");

    const options = await openColumnMenu(page, source);
    await options.getByRole("menuitem", { name: "Move to position", exact: true }).click();
    assert.equal(
      await page.getByRole("menuitem", { name: "Position 1 (current)", exact: true }).isDisabled(),
      true,
    );
    await page.getByRole("menuitem", { name: "Position 2", exact: true }).click();
    await waitOrder(initial);
    assert.equal(writes.length, 5, "The click-only position alternative saves once");
    await focused(source);
    await retained("click-position");

    await page.setViewportSize({ width: 390, height: 844 });
    touch = await context.newCDPSession(page);
    await touch.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await board.evaluate((node) => {
      node.scrollLeft = 0;
    });
    const handle = source.locator("[data-column-drag-handle]");
    const bounds = await handle.boundingBox();
    assert.ok(bounds.width >= 44 && bounds.height >= 44, "Touch drag handles retain 44px targets");
    const narrowBefore = await scrolls();
    baseline = { ...baseline, scrolls: narrowBefore };
    await handle.scrollIntoViewIfNeeded();
    const keyboardBoardBefore = await board.evaluate((node) => node.scrollLeft);
    await reorderColumn(page, source, 2);
    const narrowOrder = [
      ...initial.filter((id) => id !== columnsFeedSelections[1].id),
      columnsFeedSelections[1].id,
    ];
    await waitOrder(narrowOrder);
    assert.equal(writes.length, 6);
    assert.ok(
      await board.evaluate((node, before) => node.scrollLeft > before + 20, keyboardBoardBefore),
      "Narrow keyboard dragging scrolls the board",
    );
    assert.deepEqual(
      await scrolls(),
      narrowBefore,
      "Narrow dragging retains vertical reading positions",
    );
    await focused(source);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.screenshot({ path: `${output}-mobile.png`, animations: "disabled" });

    const boardBeforeTouch = await board.evaluate((node) => node.scrollLeft);
    const start = await handle.boundingBox();
    const edge = await board.boundingBox();
    await touch.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: start.x + start.width / 2, y: start.y + start.height / 2 }],
    });
    try {
      for (let step = 1; step <= 12; step++)
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchMove",
          touchPoints: [
            {
              x:
                start.x + start.width / 2 + ((edge.x + 12 - start.x - start.width / 2) * step) / 12,
              y: start.y + start.height / 2,
            },
          ],
        });
      await wait(
        async () => (await source.getAttribute("data-dragging")) === "true",
        "Touch input lifts the handle",
      );
      await wait(
        async () => (await board.evaluate((node) => node.scrollLeft)) < boardBeforeTouch - 20,
        "Touch dragging at the narrow edge auto-scrolls the board",
      );
    } finally {
      await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    }
    await page.locator('[aria-label="Columns workspace"][aria-busy="false"]').waitFor();
    await wait(async () => writes.length === 7, "Touch drop saves one reordered workspace");
    assert.ok(
      saved.findIndex(({ id }) => id === columnsFeedSelections[1].id) < 3,
      "The touch-dragged column moves earlier in the workspace",
    );
    await retained("touch-drop");
    const finalOrder = saved.map(({ id }) => id);
    await page.reload();
    await workspace.waitFor();
    await waitOrder(finalOrder);
    assert.deepEqual(saved.map(({ id }) => id).sort(), [...initial].sort());
    await writeFile(
      `${output}.json`,
      `${JSON.stringify({ status: "passed", passed: true, writes, snapshots, pointerPhases, finalOrder }, null, 2)}\n`,
    );
  } catch (error) {
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    const failure = `${JSON.stringify({ status: "failed", passed: false, error: error.message, writes, snapshots, pointerPhases }, null, 2)}\n`;
    await writeFile(`${output}.json`, failure);
    await writeFile(`${output}-failure.json`, failure);
    throw error;
  } finally {
    failedSave.released.resolve();
    if (touch) {
      await touch
        .send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] })
        .catch(() => {});
      await touch.send("Emulation.setTouchEmulationEnabled", { enabled: false }).catch(() => {});
      await touch.detach().catch(() => {});
    }
    await context.unroute(pattern, handler);
    await fixture.cleanup();
    await page.emulateMedia({ reducedMotion: "no-preference" });
  }
}

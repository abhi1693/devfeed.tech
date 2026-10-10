import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

async function tableGeometry(table, loading) {
  return table.evaluate((node, pending) => {
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      return { x: box.x, width: box.width, height: box.height };
    };
    const rows = [...node.querySelectorAll("tbody tr")];
    const first = rows[0];
    const title = first.querySelector(pending ? ".skeleton-line" : ".article-list-title");
    const skeleton = node.closest(".loading-skeleton");
    const status = skeleton?.closest('[role="status"]');
    return {
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      coarse: matchMedia("(pointer: coarse)").matches,
      containerWidth: node.parentElement.clientWidth,
      table: rect(node),
      headers: [...node.querySelectorAll("thead th")].map((element) => ({
        label: element.textContent.trim(),
        ...rect(element),
      })),
      cells: [...first.children].map(rect),
      rows: rows.map((row) => ({ cells: row.children.length, ...rect(row) })),
      title: {
        ...rect(title),
        lineHeight: Number.parseFloat(getComputedStyle(title).lineHeight),
        placeholders: first.children[0].querySelectorAll(".skeleton-line").length,
      },
      bookmark: rect(first.querySelector(pending ? ".skeleton-bookmark" : ".bookmark-button")),
      loading: pending
        ? {
            hidden: skeleton.getAttribute("aria-hidden"),
            label: status?.getAttribute("aria-label"),
            images: skeleton.querySelectorAll("img, .skeleton-image").length,
            actions: skeleton.querySelectorAll(
              'a[href], button, input, select, textarea, [contenteditable="true"], [tabindex]:not([tabindex="-1"])',
            ).length,
            linesPerTitle: rows.map(
              (row) => row.children[0].querySelectorAll(".skeleton-line").length,
            ),
          }
        : null,
    };
  }, loading);
}

function assertGeometry(snapshot, loading) {
  assert.ok(
    snapshot.documentWidth <= snapshot.viewport + 1,
    "Compact loading and loaded rows must not widen the page",
  );
  assert.deepEqual(
    snapshot.headers.map((header) => header.label),
    ["Article", "Source", "Published", "Bookmark"],
  );
  assert.equal(snapshot.cells.length, 4);
  assert.ok(snapshot.rows.length > 0);
  for (const row of snapshot.rows) assert.equal(row.cells, 4);
  for (const [index, cell] of snapshot.cells.entries()) {
    const header = snapshot.headers[index];
    assert.ok(
      Math.abs(cell.x - header.x) <= 1 && Math.abs(cell.width - header.width) <= 1,
      `Column ${index + 1} aligns with its heading`,
    );
  }
  const touch = snapshot.coarse || snapshot.containerWidth <= 700;
  const bookmarkSize = touch ? 44 : 32;
  assert.ok(
    Math.abs(snapshot.bookmark.width - bookmarkSize) <= 1 &&
      Math.abs(snapshot.bookmark.height - bookmarkSize) <= 1,
    `Bookmark ${loading ? "placeholder" : "control"} retains ${bookmarkSize}px dimensions`,
  );
  assert.ok(
    Math.abs(
      snapshot.bookmark.x +
        snapshot.bookmark.width / 2 -
        (snapshot.cells[3].x + snapshot.cells[3].width / 2),
    ) <= 1,
    "The bookmark placeholder and control are centered in their column",
  );
  if (!touch)
    for (const row of snapshot.rows)
      assert.ok(row.height >= 36 && row.height <= 38, "Desktop loading rows stay compact");
  if (loading) {
    assert.equal(snapshot.rows.length, 6, "Loading uses six compact rows");
    assert.equal(snapshot.loading.hidden, "true", "Decorative placeholders stay hidden from AT");
    assert.ok(snapshot.loading.label?.trim(), "The outer status announces the loading state");
    assert.equal(snapshot.loading.images, 0, "Compact loading has no card image placeholders");
    assert.equal(snapshot.loading.actions, 0, "Loading placeholders offer no fake actions");
    assert.deepEqual(snapshot.loading.linesPerTitle, [1, 1, 1, 1, 1, 1]);
    assert.ok(snapshot.title.height <= 16, "Each placeholder has a single compact title line");
  } else {
    assert.ok(
      snapshot.title.height <= snapshot.title.lineHeight + 1,
      "Loaded titles occupy a single line",
    );
  }
}

function compareTables(placeholder, loaded) {
  for (const key of ["x", "width"])
    assert.ok(
      Math.abs(placeholder.table[key] - loaded.table[key]) <= 1,
      `The loading table retains its ${key} after the feed arrives`,
    );
  for (const [index, cell] of placeholder.cells.entries())
    for (const key of ["x", "width"])
      assert.ok(
        Math.abs(cell[key] - loaded.cells[index][key]) <= 1,
        `Column ${index + 1} retains its ${key} after the feed arrives`,
      );
  for (const row of placeholder.rows)
    assert.ok(
      Math.abs(row.height - loaded.rows[0].height) <= 1,
      "Placeholder rows retain the loaded list density",
    );
  assert.ok(
    Math.abs(placeholder.bookmark.width - loaded.bookmark.width) <= 1 &&
      Math.abs(placeholder.bookmark.height - loaded.bookmark.height) <= 1,
    "Bookmark placeholders retain the real action dimensions",
  );
}

async function assertBootstrapStatus(page, stage) {
  const status = page.getByRole("status", { name: /^Loading/ }).first();
  await status.waitFor({ state: "attached" });
  assert.equal(
    await status.evaluate((node) => {
      for (let element = node; element; element = element.parentElement) {
        const style = getComputedStyle(element);
        if (
          element.hidden ||
          element.hasAttribute("inert") ||
          element.getAttribute("aria-hidden") === "true" ||
          style.display === "none" ||
          style.visibility === "hidden"
        )
          return false;
      }
      return true;
    }),
    true,
    `${stage}: the status stays available to assistive technology`,
  );
  assert.equal(
    await page
      .locator(".loading-skeleton.feed, .loading-skeleton.form, .article-card, .skeleton-list-row")
      .count(),
    0,
    `${stage}: announce loading without guessing the saved feed layout`,
  );
}

/** Hold a real personal-feed response while checking web and extension loading layouts. */
export async function checkCompactLoading(
  page,
  target,
  output,
  {
    hold,
    release,
    waitAccount,
    releaseAccount,
    waitSettings,
    releaseSettings,
    waitFeed,
    releaseFeed,
  },
) {
  const viewport = page.viewportSize();
  const reduced = await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
  const coarse = await page.evaluate(() => matchMedia("(pointer: coarse)").matches);
  const list = page.getByRole("button", { name: "List view", exact: true });
  const grid = page.getByRole("button", { name: "Grid view", exact: true });
  const pending = page.locator(".loading-skeleton.feed.compact .article-table");
  const loaded = page.locator(".article-table:has(.article-list-row)").first();
  const measurements = [];
  let held = false;
  let touch = false;
  let cdp;
  let clockPaused = false;
  const observationKey = "devfeed:test:compact-loading-observe";
  const staged = Boolean(waitAccount && releaseAccount && waitSettings && releaseSettings);
  assert.ok(
    staged || !(waitAccount || releaseAccount || waitSettings || releaseSettings),
    "Bootstrap gates must provide all four staged callbacks",
  );
  if (output) await mkdir(dirname(output), { recursive: true });
  async function waitForDOM(predicate, message) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      if (await page.evaluate(predicate)) return;
      // Poll with the runner's clock while the page's exit timer is paused.
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.fail(message);
  }
  async function checkFeedFirst() {
    await page.setViewportSize({ width: 1440, height: 1000 });
    held = true;
    await hold();
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitAccount();
    await assertBootstrapStatus(page, "Feed-first account loading");
    await releaseAccount();
    await waitSettings();
    await waitFeed();
    await assertBootstrapStatus(page, "Feed-first preference loading");
    // All three callers install a running page clock for earlier promo checks.
    // Retain the real 180ms exit fallback while changing the response order.
    await page.clock.pauseAt(new Date(await page.evaluate(() => Date.now() + 1000)));
    clockPaused = true;
    await releaseFeed();
    await waitForDOM(
      () =>
        document.querySelector(
          '.reader-loading-reveal[data-loading="false"] > .reader-loading-placeholder[aria-hidden="true"]',
        ) !== null,
      "The feed must resolve while its preferences remain blocked",
    );
    await releaseSettings();
    await waitForDOM(
      () => document.querySelector(".article-table .article-list-row") !== null,
      "The resolved preferences must render the loaded compact feed",
    );
    const transition = await page.evaluate(() => {
      const observation = window.__compactLoadingObservation;
      observation.observer.disconnect();
      return {
        wrong: observation.wrong,
        fallback: [
          ...document.querySelectorAll(
            '.reader-loading-placeholder[aria-hidden="true"] .loading-skeleton.feed',
          ),
        ].map((node) => ({
          compact: node.classList.contains("compact"),
          visibility: getComputedStyle(node).visibility,
        })),
      };
    });
    assert.deepEqual(transition.wrong, [], "Feed-first loading never flashes grid, form, or cards");
    assert.equal(
      transition.fallback.length,
      1,
      "The actual exit fallback is retained for this check",
    );
    assert.deepEqual(transition.fallback, [{ compact: true, visibility: "hidden" }]);
    const ready = await tableGeometry(loaded, false);
    assertGeometry(ready, false);
    await page.clock.resume();
    clockPaused = false;
    await release();
    held = false;
    measurements.push({ width: 1440, responseOrder: "feed-first", transition, loaded: ready });
    if (output)
      await page.screenshot({ path: `${output}-loading-feed-first.png`, animations: "disabled" });
  }
  async function check(width, pointer = "original") {
    await page.setViewportSize({ width, height: 1000 });
    held = true;
    await hold();
    await page.reload({ waitUntil: "domcontentloaded" });
    if (staged) {
      await waitAccount();
      await assertBootstrapStatus(page, "Waiting for the account");
      await releaseAccount();
      await waitSettings();
      await assertBootstrapStatus(page, "Waiting for feed preferences");
      await releaseSettings();
    }
    await pending.waitFor();
    assert.equal(await pending.count(), 1, "Only one compact skeleton appears after preferences");
    const bootstrap = await page.evaluate(() => {
      const observation = window.__compactLoadingObservation;
      observation.observer.disconnect();
      return { wrong: observation.wrong, maximumCompactSkeletons: observation.maximum };
    });
    assert.deepEqual(bootstrap.wrong, [], "Compact loading never flashes grid, form, or cards");
    assert.equal(bootstrap.maximumCompactSkeletons, 1, "Compact loading never duplicates shimmers");
    if (pointer === "coarse") {
      await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
      await cdp.send("Emulation.setEmitTouchEventsForMouse", {
        enabled: true,
        configuration: "mobile",
      });
      await waitForDOM(
        () => matchMedia("(pointer: coarse)").matches,
        "Coarse pointer emulation applies to the loaded document",
      );
    }
    const placeholder = await tableGeometry(pending, true);
    if (pointer === "coarse")
      assert.equal(placeholder.coarse, true, "The loading measurement uses a coarse pointer");
    assertGeometry(placeholder, true);
    await page.emulateMedia({ reducedMotion: "reduce" });
    const animations = await pending
      .locator(".shimmer")
      .evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node, "::after").animationName));
    assert.ok(animations.length > 0);
    assert.ok(
      animations.every((name) => name === "none"),
      "Reduced motion disables every compact loading shimmer",
    );
    if (output)
      await page.screenshot({
        path: `${output}-loading-${width}-${pointer}.png`,
        animations: "disabled",
      });
    await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
    await release();
    held = false;
    await pending.waitFor({ state: "detached" });
    await loaded.waitFor();
    if (pointer === "coarse") {
      await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
      await cdp.send("Emulation.setEmitTouchEventsForMouse", {
        enabled: true,
        configuration: "mobile",
      });
    }
    const ready = await tableGeometry(loaded, false);
    if (pointer === "coarse")
      assert.equal(ready.coarse, true, "The loaded measurement retains a coarse pointer");
    assertGeometry(ready, false);
    compareTables(placeholder, ready);
    measurements.push({ width, pointer, bootstrap, placeholder, loaded: ready, animations });
  }
  try {
    await page.goto(target);
    await page.locator('button[aria-label="List view"]:not(:disabled)').waitFor();
    await list.click();
    await loaded.waitFor();
    await page.locator('button[aria-label="List view"]:not(:disabled)').waitFor();
    await page.addInitScript((key) => {
      if (sessionStorage.getItem(key) !== "true") return;
      const observation = { wrong: [], maximum: 0, observer: null };
      const selectors = [
        ".loading-skeleton.form",
        ".loading-skeleton.feed:not(.compact) .skeleton-image",
        ".article-card",
      ];
      const inspect = (root) => {
        if (!(root instanceof Element || root instanceof Document)) return;
        for (const selector of selectors) {
          if ((root instanceof Element && root.matches(selector)) || root.querySelector(selector)) {
            if (observation.wrong.length < 20)
              observation.wrong.push({ selector, time: performance.now() });
          }
        }
      };
      observation.observer = new MutationObserver((records) => {
        inspect(document);
        for (const record of records) for (const node of record.addedNodes) inspect(node);
        observation.maximum = Math.max(
          observation.maximum,
          document.querySelectorAll(".loading-skeleton.feed.compact .article-table").length,
        );
      });
      window.__compactLoadingObservation = observation;
      observation.observer.observe(document, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    }, observationKey);
    await page.evaluate((key) => sessionStorage.setItem(key, "true"), observationKey);
    for (const width of [320, 375, 768, 1440]) await check(width);
    if (staged && waitFeed && releaseFeed) await checkFeedFirst();
    if (!coarse) {
      cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
      touch = true;
      await page.waitForFunction(() => matchMedia("(pointer: coarse)").matches);
      await check(1440, "coarse");
    }
    if (output)
      await writeFile(
        `${output}-loading.json`,
        JSON.stringify({ passed: true, measurements }, null, 2),
      );
    return measurements;
  } finally {
    if (held) await release();
    if (clockPaused) await page.clock.resume();
    await page.evaluate((key) => {
      sessionStorage.removeItem(key);
      window.__compactLoadingObservation?.observer.disconnect();
    }, observationKey);
    if (touch) {
      await cdp.send("Emulation.setEmitTouchEventsForMouse", { enabled: false });
      await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
      await page.waitForFunction(() => !matchMedia("(pointer: coarse)").matches);
      // Chromium restores its fine pointer after reloading the document.
      await page.reload({ waitUntil: "domcontentloaded" });
    }
    await cdp?.detach();
    await page.emulateMedia({ reducedMotion: reduced ? "reduce" : "no-preference" });
    if (viewport) await page.setViewportSize(viewport);
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    await grid.click();
    await page.locator(".article-card").first().waitFor();
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
  }
}

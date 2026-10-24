import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { setArticleView } from "./article-view.mjs";

export function previewRetentionArticle(article, index) {
  return {
    ...article,
    id: `44444444-4444-4444-8444-${String(index).padStart(12, "0")}`,
    slug: `preview-retention-${index}`,
    title: `Preview retention article ${index + 1}`,
    summary: "An article for checking that previews retain the loaded reader.",
    ai_summary: null,
    ai_description: null,
    image_url: null,
    image_variants: [],
  };
}

export function previewRetentionFeed(article, params) {
  const next = params.has("cursor");
  return {
    items: Array.from({ length: next ? 1 : 24 }, (_, index) =>
      previewRetentionArticle(article, next ? 24 : index),
    ),
    next_cursor: next ? null : "preview-retention-next",
    generation: "preview-retention-generation",
    has_interests: true,
    status: "ready",
    reasons: {},
  };
}

export function previewRetentionEngagement(params) {
  return params.getAll("article_id").map((article_id) => ({
    article_id,
    likes: 0,
    opens: 0,
    liked: false,
    bookmarked: true,
  }));
}

function requestCounts(requests) {
  const counts = Object.fromEntries(
    [
      "auth/me",
      "settings/feed",
      "settings/feed-view",
      "settings/profile",
      "settings/appearance",
      "settings/columns",
      "preferences",
      "preferences/sources",
      "preferences/tags",
      "engagement",
      "feed",
      "user/feed",
      "bookmarks",
    ].map((key) => [key, 0]),
  );
  for (const request of requests) {
    const path = new URL(request.url ?? request, "https://devfeed.tech").pathname.replace(
      /^\/api/,
      "",
    );
    const key =
      path === "/v1/feed" ? "feed" : path === "/v1/user/feed" ? "user/feed" : path.slice(9);
    if (Object.hasOwn(counts, key)) counts[key] += 1;
  }
  return counts;
}

export function retentionBaselineReady(selector) {
  const rows = [...document.querySelectorAll(selector)].filter((row) => row.checkVisibility());
  return (
    rows.length === 25 &&
    rows.every((row) => {
      // Compact rows render bookmarks without the like/open counters used by
      // Stacked and Grid. Guest bookmarks are links; member buttons enable only
      // after their engagement record has loaded.
      if (!row.matches(".article-list-row")) return row.querySelector(".engagement-count");
      const bookmark = row.querySelector(".bookmark-button");
      return bookmark?.matches("a[href]") || (bookmark?.matches("button") && !bookmark.disabled);
    }) &&
    !document.querySelector(".reader-loading-placeholder") &&
    document.fonts.status === "loaded"
  );
}

/** Capture the baseline after hydration and native scrolling have stopped moving it. */
export function stableRetentionPosition(node) {
  return new Promise((resolve, reject) => {
    const started = performance.now();
    let stableSince = started;
    let previous;
    const measure = () => {
      const bounds = node.getBoundingClientRect();
      const position = [bounds.y, bounds.height, scrollY, document.documentElement.scrollHeight];
      const key = JSON.stringify(position);
      const now = performance.now();
      if (key !== previous) {
        previous = key;
        stableSince = now;
      }
      if (now - stableSince >= 200) resolve(position);
      else if (now - started >= 5000)
        reject(new Error(`Retention baseline did not settle: ${key}`));
      else requestAnimationFrame(measure);
    };
    requestAnimationFrame(measure);
  });
}

async function previewDuringNativeScroll(page, title, target) {
  await page.bringToFront();
  // Activate the actual link in the first moving browser frame. A driver roundtrip
  // can miss the entire animation on a busy runner; pointer preparation would
  // instead scroll the moving target back into place before clicking it.
  const motion = await title.evaluate((node) => {
    node.focus({ preventScroll: true });
    const start = scrollY;
    const target = Math.max(0, start - 600);
    return new Promise((resolve, reject) => {
      let frame;
      const timeout = setTimeout(() => {
        cancelAnimationFrame(frame);
        reject(
          new Error(
            `Native scroll did not advance: ${JSON.stringify({ start, target, scroll: scrollY })}`,
          ),
        );
      }, 5000);
      const activate = () => {
        if (start - scrollY >= 5) {
          clearTimeout(timeout);
          const activated = scrollY;
          node.click();
          resolve({ start, target, activated });
        } else frame = requestAnimationFrame(activate);
      };
      scrollTo({ top: target, behavior: "smooth" });
      frame = requestAnimationFrame(activate);
    });
  });
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  await preview.waitFor();
  // Observe after the native animation would otherwise have moved the retained document.
  await page.waitForTimeout(150);
  const opened = await page.evaluate(() => scrollY);
  await preview.getByRole("button", { name: "Close preview", exact: true }).click();
  await preview.waitFor({ state: "hidden" });
  await page.waitForURL(target);
  await page.waitForTimeout(350);
  const closed = await page.evaluate(() => scrollY);
  return { ...motion, scroll: opened, closed };
}

/** Observe real components throughout preview navigation, including transient loading states. */
export async function checkPreviewFeedRetention(
  page,
  target,
  output,
  { requests = () => [], baseline = false, resumeOnly = false } = {},
) {
  const results = [];
  for (const view of ["stacked", "cards", "compact"]) {
    // Extension pagination intentionally waits for the active browser window.
    await page.bringToFront();
    await page.goto(target);
    await setArticleView(page, view, { returnTo: target });
    const rowSelector = {
      stacked: ".article-stacked-row:not(.partner-placement)",
      cards: ".article-card:not(.partner-placement)",
      compact: ".article-list-row:not(.partner-placement)",
    }[view];
    const titleSelector = {
      stacked: ".article-stacked-title",
      cards: ".card-open-link",
      compact: ".article-list-title",
    }[view];
    const rows = page.locator(`${rowSelector}:visible`);
    await rows.first().waitFor();
    // Real input cancels pending route scroll restoration before positioning the pagination boundary.
    await page.mouse.wheel(0, await page.evaluate(() => document.documentElement.scrollHeight));
    try {
      await page.waitForFunction(
        (selector) => {
          if (
            [...document.querySelectorAll(selector)].filter((row) => row.checkVisibility())
              .length === 25
          )
            return true;
          // The extension restores route scroll after mount; keep the sentinel in view until page 2 loads.
          scrollTo(0, document.documentElement.scrollHeight);
          return false;
        },
        rowSelector,
        { polling: 100 },
      );
    } catch (error) {
      console.error("Preview retention pagination setup", {
        view,
        target,
        state: await page.evaluate(
          (selector) => ({
            focused: document.hasFocus(),
            rows: [...document.querySelectorAll(selector)].filter((row) => row.checkVisibility())
              .length,
            pagination: [...document.querySelectorAll(".pagination")].map((node) => ({
              html: node.outerHTML,
              bounds: node.getBoundingClientRect().toJSON(),
            })),
            scroll: scrollY,
            height: document.documentElement.scrollHeight,
            text: document.body.innerText.slice(-600),
          }),
          rowSelector,
        ),
        requests: requests().slice(-15),
      });
      throw error;
    }
    await page.locator('.pagination[data-has-more="false"]').last().waitFor();
    const title = rows.nth(18).locator(titleSelector).first();
    // Keep the trigger clear of the sticky header before recording scroll; Playwright otherwise
    // legitimately scrolls an obscured Stacked title while preparing its click.
    await title.evaluate((node) => node.scrollIntoView({ block: "center", behavior: "instant" }));
    // A fixed delay can record the baseline while a late engagement response or
    // placeholder exit is still changing row heights on a busy browser runner.
    await page.waitForFunction(retentionBaselineReady, rowSelector);
    await title.evaluate(stableRetentionPosition);
    if (resumeOnly) {
      const resumedRows = await rows.elementHandles();
      const resumeBounds = await title.boundingBox();
      const beforeScroll = await page.evaluate(() => scrollY);
      const resumeRequestStart = requests().length;
      const resumeTime = await page.evaluate(() => Date.now() + 120_000);
      await page.clock.setFixedTime(resumeTime);
      const resumedAuth = page.waitForResponse((response) =>
        new URL(response.url()).pathname.endsWith("/user/auth/me"),
      );
      await page.evaluate(() => {
        window.dispatchEvent(new Event("focus"));
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await resumedAuth;
      await page.waitForTimeout(300);
      await page.waitForFunction(retentionBaselineReady, rowSelector);
      const rowCount = await rows.count();
      const connected = [];
      for (const row of resumedRows) {
        connected.push(await row.evaluate((node) => node.isConnected));
        await row.dispose();
      }
      const resumedBounds = await title.boundingBox();
      results.push({
        view,
        retained: connected.filter(Boolean).length,
        rowCount,
        readingPositionDelta: resumedBounds ? resumedBounds.y - resumeBounds.y : null,
        before: { bounds: resumeBounds, scroll: beforeScroll },
        after: { bounds: resumedBounds, scroll: await page.evaluate(() => scrollY) },
        requests: requestCounts(requests().slice(resumeRequestStart)),
      });
      // Preserve the failing scenario too; assertions previously left no artifact
      // for the layout that moved, making a race indistinguishable from a regression.
      if (output) {
        await mkdir(dirname(output), { recursive: true });
        await writeFile(output, JSON.stringify(results, null, 2));
      }
      assert.equal(rowCount, 25, "Tab resume retains every paginated article");
      assert.ok(connected.every(Boolean), "Tab resume retains the original article DOM");
      assert.ok(resumedBounds, "Tab resume retains a visible article title");
      // Engagement controls can change line height during hydration. Retain the
      // same article within one text line rather than requiring an identical pixel.
      assert.ok(
        Math.abs(resumedBounds.y - resumeBounds.y) <= 24,
        `Tab resume retains the reading position (${target}, ${view}, before=${resumeBounds.y}, after=${resumedBounds.y})`,
      );
      continue;
    }
    const requestStart = requests().length;
    const countBefore = requestCounts(requests());
    const triggerBounds = await title.boundingBox();
    const before = await page.evaluate(
      ({ rowSelector }) => {
        const rows = [...document.querySelectorAll(rowSelector)].filter((row) =>
          row.checkVisibility(),
        );
        const state = { rows, events: [], scroll: scrollY, timeline: [] };
        const recordPointer = () => state.timeline.push({ phase: "pointerdown", scroll: scrollY });
        const recordNavigation = () =>
          state.timeline.push({ phase: "navigationStarted", scroll: scrollY });
        window.addEventListener("pointerdown", recordPointer, { once: true });
        window.addEventListener("devfeed:reader-navigation", recordNavigation, { once: true });
        state.cleanup = () => {
          window.removeEventListener("pointerdown", recordPointer);
          window.removeEventListener("devfeed:reader-navigation", recordNavigation);
        };
        state.observer = new MutationObserver((records) => {
          for (const record of records) {
            if (record.type === "childList") {
              for (const removed of record.removedNodes)
                if (state.rows.some((row) => removed === row || removed.contains?.(row)))
                  state.events.push("loaded-row-removed");
              for (const added of record.addedNodes)
                if (
                  added.nodeType === Node.ELEMENT_NODE &&
                  (added.matches(".reader-loading-placeholder, .loading-skeleton") ||
                    added.querySelector(".reader-loading-placeholder, .loading-skeleton"))
                )
                  state.events.push("loading-placeholder-added");
            }
            if (
              record.type === "attributes" &&
              record.attributeName === "data-loading" &&
              record.target.getAttribute("data-loading") === "true"
            )
              state.events.push("loading-restarted");
            if (
              record.type === "attributes" &&
              ["hidden", "style"].includes(record.attributeName) &&
              state.rows.some((row) => record.target === row || record.target.contains(row)) &&
              (record.target.hidden || getComputedStyle(record.target).display === "none")
            )
              state.events.push("loaded-rows-hidden");
          }
        });
        state.observer.observe(document.body, {
          childList: true,
          subtree: true,
          attributes: true,
          attributeFilter: ["data-loading", "hidden", "style"],
        });
        window.__previewRetention = state;
        return {
          count: rows.length,
          scroll: scrollY,
          href: location.href,
          hasMore: [...document.querySelectorAll(".pagination")].find((node) =>
            node.checkVisibility(),
          )?.dataset.hasMore,
        };
      },
      { rowSelector },
    );
    await title.click();
    const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
    await preview.waitFor();
    await page.waitForTimeout(100);
    const openState = await page.evaluate(() => ({
      scroll: scrollY,
      historyScroll: history.state?.readerScroll,
    }));
    const countOpen = requestCounts(requests());
    await preview.getByRole("button", { name: "Close preview", exact: true }).click();
    await preview.waitFor({ state: "hidden" });
    await page.waitForURL(target);
    // Auth/preference effects finish after navigation commits; inspecting only the final row misses flashes.
    await page.waitForTimeout(350);
    const after = await page.evaluate(() => {
      const state = window.__previewRetention;
      state.observer.disconnect();
      state.cleanup();
      const result = {
        retained: state.rows.filter((row) => row.isConnected).length,
        visible: state.rows.filter((row) => row.checkVisibility()).length,
        events: state.events,
        scroll: scrollY,
        href: location.href,
        hasMore: [...document.querySelectorAll(".pagination")].find((node) =>
          node.checkVisibility(),
        )?.dataset.hasMore,
        timeline: state.timeline,
      };
      delete window.__previewRetention;
      return result;
    });
    const countAfter = requestCounts(requests());
    const engagement = requests()
      .slice(requestStart)
      .flatMap((request) => {
        const url = new URL(request.url ?? request, "https://devfeed.tech");
        return url.pathname.endsWith("/user/engagement")
          ? [url.searchParams.getAll("article_id")]
          : [];
      });
    const delta = (left, right) =>
      Object.fromEntries(Object.keys(left).map((key) => [key, right[key] - left[key]]));
    results.push({
      view,
      target,
      before,
      triggerBounds,
      openState,
      after,
      openRequests: delta(countBefore, countOpen),
      closeRequests: delta(countOpen, countAfter),
      engagement,
      nativeScroll: view === "cards" ? await previewDuringNativeScroll(page, title, target) : null,
    });
  }
  if (output) {
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, JSON.stringify(results, null, 2));
  }
  if (resumeOnly) {
    console.log(`Tab resume retention ${target}: ${JSON.stringify(results)}`);
    return results;
  }
  console.log(
    `Preview retention ${target}: ${JSON.stringify(
      results.map(({ view, after, openRequests, closeRequests }) => ({
        view,
        retained: after.retained,
        events: after.events,
        openRequests,
        closeRequests,
      })),
    )}`,
  );
  if (!baseline)
    for (const result of results) {
      const label = `${result.view} ${target}`;
      assert.equal(result.after.retained, result.before.count, `${label}: loaded DOM rows survive`);
      assert.equal(
        result.after.visible,
        result.before.count,
        `${label}: all loaded pages remain visible`,
      );
      assert.deepEqual(
        result.after.events,
        [],
        `${label}: no transient list removal, hiding, or skeleton`,
      );
      assert.equal(result.after.href, result.before.href, `${label}: filters remain unchanged`);
      assert.equal(
        result.after.hasMore,
        result.before.hasMore,
        `${label}: the exhausted cursor stays exhausted`,
      );
      // CDP wheel/click preparation may finish scrolling before pointerdown, especially in Edge.
      // Navigation must preserve the position at the user's actual click.
      const clickedScroll = result.after.timeline.find(
        (event) => event.phase === "pointerdown",
      )?.scroll;
      assert.equal(
        typeof clickedScroll,
        "number",
        `${label}: the real preview trigger received pointer input`,
      );
      assert.ok(
        Math.abs(result.after.scroll - clickedScroll) <= 2,
        `${label}: scroll position remains unchanged`,
      );
      assert.ok(
        Math.abs(result.openState.scroll - clickedScroll) <= 2,
        `${label}: opening retains the clicked row's scroll position`,
      );
      if (result.nativeScroll) {
        const { start, target, activated, scroll, closed } = result.nativeScroll;
        assert.equal(
          typeof activated,
          "number",
          `${label}: native motion activates the preview link`,
        );
        assert.ok(
          start - activated >= 5 && activated - target > 2,
          `${label}: the preview opens while native scrolling is still in flight`,
        );
        assert.ok(
          Math.abs(scroll - activated) <= 2,
          `${label}: opening cancels in-flight native scrolling`,
        );
        assert.ok(
          Math.abs(closed - activated) <= 2,
          `${label}: dismissal retains the position where scrolling was interrupted`,
        );
      }
      for (const [phase, counts] of [
        ["open", result.openRequests],
        ["close", result.closeRequests],
      ]) {
        const expected = requestCounts([]);
        if (phase === "open") expected.engagement = 1;
        expected["auth/me"] = counts["auth/me"];
        assert.ok(counts["auth/me"] <= 1, `${label}: ${phase} checks the session at most once`);
        assert.deepEqual(
          counts,
          expected,
          `${label}: ${phase} retains the loaded reader; only the opened preview reads its own engagement`,
        );
      }
      assert.deepEqual(
        result.engagement.map((ids) => ids.length),
        [1],
        `${label}: only the opened article's engagement is fetched`,
      );
    }
  return results;
}

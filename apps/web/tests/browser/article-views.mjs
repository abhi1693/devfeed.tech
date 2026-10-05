import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

export const compactTitle =
  "Unicode boundaries — 日本語, العربية, café, 👩🏽‍💻 and TypeScript: " +
  "building dependable applications with unusually long article titles and useful details "
    .repeat(3)
    .trim();

export function compactListArticle(article, index = 0) {
  return {
    ...article,
    title: `${compactTitle}${index ? ` (${index + 1})` : ""}`,
    sources:
      index % 2
        ? []
        : [
            {
              ...(article.sources[0] ?? {
                id: "compact-source",
                slug: "compact-source",
                logo_url: null,
              }),
              name: "Engineering 日本語 — international architecture and reliability journal",
            },
          ],
    canonical_url:
      index % 2
        ? "https://a-very-long-publisher-hostname.example.test/article"
        : article.canonical_url,
    published_at: index % 2 ? null : "2020-02-29T23:59:59Z",
    feed_at: "2026-12-31T00:00:00Z",
  };
}

/** Hold client requests without replacing the web or extension fixture response. */
export function compactLoadingGate(page) {
  const pattern = /\/api\/v1\/user\/(?:auth\/me|settings\/feed|feed)(?:\?|$)/;
  let stages;
  let handler;
  async function waitForStage(name) {
    let timer;
    try {
      await Promise.race([
        stages[name].requested,
        new Promise((_, reject) => {
          timer = setTimeout(
            () => reject(new Error(`The ${name} loading request was not made`)),
            15000,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }
  return {
    async hold() {
      assert.equal(stages, undefined, "Only one compact loading gate is active");
      stages = Object.fromEntries(
        ["account", "settings", "feed"].map((name) => {
          const stage = {};
          stage.requested = new Promise((resolve) => {
            stage.markRequested = resolve;
          });
          stage.barrier = new Promise((resolve) => {
            stage.release = resolve;
          });
          return [name, stage];
        }),
      );
      handler = async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        const name = pathname.endsWith("auth/me")
          ? "account"
          : pathname.endsWith("settings/feed")
            ? "settings"
            : "feed";
        const stage = stages[name];
        stage.markRequested();
        await stage.barrier;
        await route.fallback();
      };
      await page.route(pattern, handler);
    },
    waitAccount: () => waitForStage("account"),
    releaseAccount: () => stages.account.release(),
    waitSettings: () => waitForStage("settings"),
    releaseSettings: () => stages.settings.release(),
    waitFeed: () => waitForStage("feed"),
    releaseFeed: () => stages.feed.release(),
    async release() {
      for (const stage of Object.values(stages ?? {})) stage.release();
      stages = undefined;
      if (handler) await page.unroute(pattern, handler);
      handler = undefined;
    },
  };
}

async function listGeometry(page) {
  return page
    .locator(".article-table:has(.article-list-row)")
    .first()
    .evaluate((table) => {
      const rect = (element) => {
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {
          x: box.x,
          right: box.right,
          width: box.width,
          height: box.height,
          align: style.textAlign,
        };
      };
      const row = table.querySelector("tbody tr");
      const title = row.querySelector(".article-list-title");
      const titleStyle = getComputedStyle(title);
      return {
        width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
        containerWidth: table.parentElement.clientWidth,
        coarse: matchMedia("(pointer: coarse)").matches,
        row: rect(row),
        headers: [...table.querySelectorAll("thead th")].map((element) => ({
          text: element.textContent.trim(),
          ...rect(element),
        })),
        cells: [...row.children].map(rect),
        bookmark: rect(row.querySelector(".bookmark-button")),
        title: {
          ...rect(title),
          whiteSpace: titleStyle.whiteSpace,
          overflow: titleStyle.overflow,
          ellipsis: titleStyle.textOverflow,
          lineHeight: parseFloat(titleStyle.lineHeight),
          scrollWidth: title.scrollWidth,
          clientWidth: title.clientWidth,
        },
      };
    });
}

function assertListGeometry(snapshot) {
  assert.ok(
    snapshot.scrollWidth <= snapshot.width + 1,
    "The list has no page-level horizontal scroll",
  );
  assert.deepEqual(
    snapshot.headers.map((item) => item.text),
    ["Article", "Source", "Published", "Bookmark"],
  );
  assert.equal(snapshot.cells.length, 4);
  for (const [index, cell] of snapshot.cells.entries()) {
    const heading = snapshot.headers[index];
    assert.ok(
      Math.abs(cell.x - heading.x) < 1 && Math.abs(cell.width - heading.width) < 1,
      `Column ${index + 1} headers and cells align`,
    );
  }
  assert.equal(snapshot.headers[2].align, "right");
  assert.equal(snapshot.cells[2].align, "right");
  assert.equal(snapshot.headers[3].align, "center");
  assert.equal(snapshot.cells[3].align, "center");
  assert.ok(
    Math.abs(
      snapshot.bookmark.x +
        snapshot.bookmark.width / 2 -
        (snapshot.cells[3].x + snapshot.cells[3].width / 2),
    ) < 1,
    "Bookmarks are centered in their column",
  );
  assert.equal(snapshot.title.whiteSpace, "nowrap");
  assert.equal(snapshot.title.overflow, "hidden");
  assert.equal(snapshot.title.ellipsis, "ellipsis");
  assert.ok(snapshot.title.height <= snapshot.title.lineHeight + 1, "Article titles use one line");
  if (snapshot.coarse || snapshot.containerWidth <= 700) {
    assert.ok(
      snapshot.bookmark.width >= 44 && snapshot.bookmark.height >= 44,
      "Touch and narrow list bookmarks retain 44px targets",
    );
  } else {
    assert.ok(
      snapshot.bookmark.width >= 24 && snapshot.bookmark.height >= 24,
      "Desktop bookmark targets remain usable",
    );
    assert.ok(
      snapshot.row.height >= 36 && snapshot.row.height <= 38,
      `Desktop rows remain dense (${snapshot.row.height}px)`,
    );
  }
}

/** Run against the production renderer in web and both unpacked extensions. */
export async function checkArticleViews(page, target, output, routes = [], { fixture } = {}) {
  const original = {
    viewport: page.viewportSize(),
    dark: await page.evaluate(() => document.documentElement.classList.contains("dark")),
    sidebar: await page.evaluate(() => localStorage.getItem("devfeed:sidebar-expanded")),
  };
  const measurements = [];
  const reloads = [];
  const record = (request) => {
    const url = new URL(request.url());
    if (
      /\/api\/v1\/(?:feed|user\/(?:feed|bookmarks|trending))$/.test(url.pathname) &&
      !url.searchParams.get("cursor")
    )
      reloads.push(url.href);
  };
  const grid = page.getByRole("button", { name: "Grid view", exact: true });
  const list = page.getByRole("button", { name: "List view", exact: true });
  const cdp = await page.context().newCDPSession(page);
  try {
    fixture?.(true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(target);
    if (fixture) {
      // Hash navigation preserves the extension's in-memory feed cache. Reload
      // after discarding its persisted copy so this fixture reaches the renderer.
      await page.evaluate(() => sessionStorage.removeItem("devfeed:public-reader-cache"));
      await page.reload();
    }
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    await grid.click();
    await page.locator(".article-card").first().waitFor();
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    const originalLinks = await page
      .locator(".card-open-link")
      .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
    const cardHeight = (await page.locator(".article-card").first().boundingBox()).height;
    page.on("request", record);
    await list.focus();
    await page.keyboard.press("Enter");
    await page.locator(".article-list-row").first().waitFor();
    await page.locator('button[aria-label="List view"]:not(:disabled)').waitFor();
    assert.equal(await list.getAttribute("aria-pressed"), "true");
    const links = await page
      .locator(".article-list-title")
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
    assert.ok(
      originalLinks.every((href) => links.includes(href)),
      "Switching retains all loaded articles",
    );
    assert.equal(
      await page
        .locator(
          ".article-table .article-list-meta, .article-table .article-list-mobile-source, .article-table .article-engagement, .article-table .open-count, .article-table .heart-button, .article-table .recommendation-reason",
        )
        .count(),
      0,
      "List rows only contain article, source, date and bookmark",
    );
    const title = page.locator(".article-list-title").first();
    const fullTitle = await title.textContent();
    assert.equal(await title.getAttribute("title"), fullTitle);
    await page.getByRole("link", { name: fullTitle, exact: true }).first().waitFor();
    if (fixture)
      assert.equal(fullTitle, compactTitle, "The actual feed receives the long Unicode fixture");
    for (const expanded of [false, true]) {
      await page.evaluate((value) => {
        localStorage.setItem("devfeed:sidebar-expanded", String(value));
        window.dispatchEvent(new Event("devfeed:sidebar-state-change"));
      }, expanded);
      await page.waitForFunction(
        (state) => document.documentElement.dataset.sidebarState === state,
        expanded ? "expanded" : "collapsed",
      );
      for (const dark of [false, true]) {
        await page.evaluate(
          (value) => document.documentElement.classList.toggle("dark", value),
          dark,
        );
        for (const width of [320, 375, 768, 1440]) {
          await page.setViewportSize({ width, height: 1000 });
          const snapshot = await listGeometry(page);
          assertListGeometry(snapshot);
          assert.ok(
            snapshot.row.height < cardHeight / 2,
            "Rows are substantially shorter than grid cards",
          );
          if (fixture)
            assert.ok(
              snapshot.title.scrollWidth > snapshot.title.clientWidth,
              "Long titles are visually truncated without shortening accessible text",
            );
          measurements.push({ expanded, dark, ...snapshot });
          const toggle = await list.boundingBox();
          assert.ok(toggle.width >= 44 && toggle.height >= 44, "View controls retain 44px targets");
          if (output && [320, 1440].includes(width))
            await page.screenshot({
              path: `${output}-list-${dark ? "dark" : "light"}-${expanded ? "expanded" : "collapsed"}-${width}.png`,
              animations: "disabled",
            });
        }
      }
    }
    if (output) await writeFile(`${output}-list.json`, JSON.stringify(measurements, null, 2));
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await page.waitForFunction(() => matchMedia("(pointer: coarse)").matches);
    const coarse = await listGeometry(page);
    assertListGeometry(coarse);
    measurements.push({ touch: true, ...coarse });
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
    // Chromium reports pointer:none after disabling CDP touch until reload.
    // The wide, fine-pointer geometry was checked before touch was enabled.
    await page.waitForFunction(() => !matchMedia("(pointer: coarse)").matches);
    const bookmark = page.locator(".article-table .bookmark-button").first();
    const edgeBookmark = page.locator(".article-table .bookmark-button").last();
    await page.keyboard.press("Tab");
    await edgeBookmark.focus();
    const outline = await edgeBookmark.evaluate((node) => ({
      style: getComputedStyle(node).outlineStyle,
      offset: parseFloat(getComputedStyle(node).outlineOffset),
    }));
    assert.notEqual(outline.style, "none", "Keyboard focus remains visible on the final row");
    assert.ok(outline.offset <= 0, "The final bookmark focus ring stays inside the clipped table");
    await bookmark.focus();
    assert.equal(await bookmark.evaluate((node) => node === document.activeElement), true);
    const pressed = await bookmark.getAttribute("aria-pressed");
    if (pressed !== null && !target.endsWith("/read-later")) {
      for (const state of [pressed === "true" ? "false" : "true", pressed]) {
        await page.keyboard.press("Space");
        await page.waitForFunction(
          (expected) =>
            document
              .querySelector(".article-table .bookmark-button")
              ?.getAttribute("aria-pressed") === expected,
          state,
        );
        await page
          .locator(".article-table .bookmark-button:not([aria-busy='true'])")
          .first()
          .waitFor();
        assert.equal(
          await bookmark.evaluate((node) => node === document.activeElement),
          true,
          "Bookmark updates retain keyboard focus",
        );
      }
    } else if (pressed === null) {
      assert.match(await bookmark.getAttribute("href"), /\/login\?/);
      await bookmark.evaluate((node) =>
        node.addEventListener(
          "click",
          (event) => {
            event.preventDefault();
            node.dataset.keyboardActivated = "true";
          },
          { once: true },
        ),
      );
      await page.keyboard.press("Enter");
      assert.equal(await bookmark.getAttribute("data-keyboard-activated"), "true");
    }
    const row = await page.locator(".article-list-row").first().elementHandle();
    await title.focus();
    await page.keyboard.press("Enter");
    await page.locator("#article-preview-title").waitFor();
    assert.equal(await row.evaluate((node) => node.isConnected), true);
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page.locator("dialog.article-modal").waitFor({ state: "detached" });
    assert.equal(await row.evaluate((node) => node.isConnected), true);
    await row.dispose();
    assert.equal(
      reloads.length,
      0,
      `View and preview changes retain the loaded feed: ${JSON.stringify(reloads)}`,
    );
    page.off("request", record);
    for (const route of routes) {
      await page.goto(route);
      await page.locator(".article-list-row").first().waitFor();
      assert.equal(
        await list.getAttribute("aria-pressed"),
        "true",
        "List preference is shared across feeds",
      );
      assertListGeometry(await listGeometry(page));
    }
    await page.reload();
    await page.locator(".article-list-row").first().waitFor();
    assert.equal(
      await list.getAttribute("aria-pressed"),
      "true",
      "List preference survives reload",
    );
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    await grid.click();
    await page.locator(".article-card").first().waitFor();
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    assert.equal(await grid.getAttribute("aria-pressed"), "true");
    await page.reload();
    await page.locator(".article-card").first().waitFor();
    assert.equal(
      await grid.getAttribute("aria-pressed"),
      "true",
      "Grid preference survives reload",
    );
    if (output) {
      await page.screenshot({ path: `${output}-grid.png`, animations: "disabled" });
      await writeFile(`${output}-list.json`, JSON.stringify(measurements, null, 2));
    }
  } finally {
    page.off("request", record);
    fixture?.(false);
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false }).catch(() => {});
    await cdp.detach();
    await page.setViewportSize(original.viewport);
    await page.evaluate(({ dark, sidebar }) => {
      document.documentElement.classList.toggle("dark", dark);
      if (sidebar === null) localStorage.removeItem("devfeed:sidebar-expanded");
      else localStorage.setItem("devfeed:sidebar-expanded", sidebar);
      window.dispatchEvent(new Event("devfeed:sidebar-state-change"));
    }, original);
    if (fixture) await page.reload();
  }
}

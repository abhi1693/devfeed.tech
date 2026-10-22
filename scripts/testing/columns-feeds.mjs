import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { openArticleViewSettings, readerRouteUrl } from "./article-view.mjs";
import { columnAction, columnsCatalog, openColumnMenu } from "./columns-workspace.mjs";
import {
  columnsFeedSelections,
  columnsFixtureArticle,
  columnsSharedArticle,
  columnsStressSelections,
  installColumnsFeedFixture,
} from "./columns-feed-fixture.mjs";

const guestKey = "devfeed:reader-preferences:guest";
const rowSelector = ".article-stacked-row:not(.partner-placement)";
const sharedSelector = `[data-article-id="${columnsSharedArticle.id}"]`;
const wait = async (predicate, label) => {
  const deadline = Date.now() + 15000;
  while (!(await predicate())) {
    assert.ok(Date.now() < deadline, label);
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
};

async function beginResourceProfile(page) {
  await page.evaluate(() => {
    const supported = PerformanceObserver.supportedEntryTypes.includes("longtask");
    const profile = {
      started: performance.now(),
      supported,
      count: 0,
      totalDuration: 0,
      maximumDuration: 0,
      entries: [],
      observer: null,
    };
    if (supported) {
      profile.observer = new PerformanceObserver((list) => {
        for (const { startTime, duration } of list.getEntries()) {
          profile.count++;
          profile.totalDuration += duration;
          profile.maximumDuration = Math.max(profile.maximumDuration, duration);
          if (profile.entries.length < 1000) profile.entries.push({ startTime, duration });
        }
      });
      profile.observer.observe({ type: "longtask" });
    }
    window.__columnResourceProfile = profile;
  });
  const proof = {
    viewport: page.viewportSize(),
    bounds: { columns: 12, pagesPerColumn: 3, articlesPerColumn: 72, totalArticles: 864 },
    samples: [],
    cdp: { available: false },
  };
  let session;
  try {
    session = await page.context().newCDPSession(page);
    await session.send("Performance.enable");
    proof.cdp = { available: true, browser: await session.send("Browser.getVersion") };
  } catch (error) {
    proof.cdp.reason = error.message;
  }
  return {
    proof,
    async sample(stage) {
      const observed = await page.evaluate(() => {
        const profile = window.__columnResourceProfile;
        return {
          elapsedMs: performance.now() - profile.started,
          articleRows: document.querySelectorAll(
            '[aria-label="Columns workspace"] .article-stacked-row:not(.partner-placement)',
          ).length,
          domElements: document.getElementsByTagName("*").length,
          longTasks: {
            supported: profile.supported,
            count: profile.count,
            totalDurationMs: profile.totalDuration,
            maximumDurationMs: profile.maximumDuration,
            entries: profile.entries,
          },
          memory: performance.memory
            ? {
                usedJSHeapSize: performance.memory.usedJSHeapSize,
                totalJSHeapSize: performance.memory.totalJSHeapSize,
                jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
              }
            : null,
          environment: {
            userAgent: navigator.userAgent,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemoryGiB: navigator.deviceMemory ?? null,
          },
        };
      });
      const sample = { stage, ...observed };
      if (proof.cdp.available) {
        sample.domCounters = await session.send("Memory.getDOMCounters");
        const { metrics } = await session.send("Performance.getMetrics");
        sample.metrics = Object.fromEntries(
          metrics
            .filter(({ name }) =>
              /^(?:JSHeap(?:Used|Total)Size|Layout(?:Count|Duration)|RecalcStyle(?:Count|Duration)|ScriptDuration|TaskDuration|Nodes|Documents|JSEventListeners)$/.test(
                name,
              ),
            )
            .map(({ name, value }) => [name, value]),
        );
      }
      proof.samples.push(sample);
    },
    async cleanup() {
      await page
        .evaluate(() => {
          window.__columnResourceProfile?.observer?.disconnect();
          delete window.__columnResourceProfile;
        })
        .catch(() => {});
      await session?.detach().catch(() => {});
    },
  };
}

/** Exercise independently loaded columns in the production website and both built readers. */
export async function checkColumnsFeeds(
  page,
  target,
  output,
  { signedIn = false, slowPreview = false } = {},
) {
  assert.equal(signedIn, true, "Columns feeds belong to signed-in My feed");
  const previewDiagnostic = slowPreview ? "slow" : process.env.DEVFEED_COLUMNS_PREVIEW_DIAGNOSTIC;
  const fixture = await installColumnsFeedFixture(page, { signedIn });
  let visualTouchSession = null;
  let resources = null;
  const navigationRequests = [];
  const recordRequest = (request) => {
    if (
      /\/api\/v1\/user\/(?:auth\/me|settings|feed)/.test(request.url()) ||
      request.isNavigationRequest() ||
      new URL(request.url()).searchParams.has("_rsc")
    )
      navigationRequests.push({
        url: request.url(),
        method: request.method(),
        type: request.resourceType(),
      });
  };
  page.on("request", recordRequest);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const column = (id) => workspace.locator(`[data-column-id="${id}"]`);
  const [latest, source, topic, tag, recommended] = columnsFeedSelections.map(({ id }) =>
    column(id),
  );
  const count = (locator) => locator.locator(rowSelector).count();
  const waitRows = async (locator, total) =>
    wait(async () => (await count(locator)) === total, `Expected ${total} independent rows`);
  const seed = async (columns) => {
    fixture.setColumns(columns);
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
    if (!signedIn)
      await page.evaluate(
        ({ key, columns }) =>
          localStorage.setItem(key, JSON.stringify({ version: 1, view: "columns", columns })),
        { key: guestKey, columns },
      );
    await page.goto(target);
    await workspace.waitFor();
  };
  const recordStableRows = async () => {
    await page.evaluate(
      (ids) => {
        window.__stableColumnRows = ids.map((id) => ({
          id,
          rows: [...document.querySelectorAll(`[data-column-id="${id}"] .article-stacked-row`)],
        }));
      },
      columnsFeedSelections.slice(0, 2).map(({ id }) => id),
    );
  };
  const assertStableRows = async () => {
    assert.equal(
      await page.evaluate(() =>
        window.__stableColumnRows?.every(({ id, rows }) =>
          rows.every(
            (row, index) =>
              row.isConnected &&
              document.querySelectorAll(`[data-column-id="${id}"] .article-stacked-row`)[index] ===
                row,
          ),
        ),
      ),
      true,
      "Unrelated columns retain their original article DOM and order",
    );
  };
  const edit = async (locator, name, kind, entity) => {
    await columnAction(page, locator, `Change feed for ${name} column`);
    const dialog = page.getByRole("dialog", { name: "Edit column", exact: true });
    await dialog.getByRole("combobox", { name: "Feed type", exact: true }).selectOption(kind);
    await dialog.getByLabel(`Find a ${kind}`, { exact: true }).fill(entity.name);
    await dialog.getByRole("button", { name: entity.name, exact: true }).click();
    await dialog.getByRole("button", { name: "Save column", exact: true }).click();
    await dialog.waitFor({ state: "hidden" });
  };
  await mkdir(dirname(output), { recursive: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    if (process.env.DEVFEED_COLUMNS_VISUAL_ARTICLES) {
      const body = JSON.parse(await readFile(process.env.DEVFEED_COLUMNS_VISUAL_ARTICLES, "utf8"));
      assert.ok(body.items.length >= 8);
      fixture.setVisualArticles(body.items.slice(0, 12));
      await seed([columnsFeedSelections.at(-1), ...columnsFeedSelections.slice(0, -1)]);
      await Promise.all(columnsFeedSelections.map(({ id }) => waitRows(column(id), 12)));
      const visual = [];
      visualTouchSession = await page.context().newCDPSession(page);
      for (const width of [1440, 1920, 2560, 390])
        for (const theme of ["light", "dark"]) {
          await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
          await visualTouchSession.send("Emulation.setTouchEmulationEnabled", {
            enabled: width === 390,
            ...(width === 390 ? { maxTouchPoints: 1 } : {}),
          });
          await page.emulateMedia({ colorScheme: theme });
          await page.evaluate(() => {
            localStorage.setItem("devfeed:theme", "system");
            window.dispatchEvent(
              new StorageEvent("storage", { key: "devfeed:theme", newValue: "system" }),
            );
            document.querySelector('[aria-label="Column feeds"]').scrollLeft = 0;
            if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
            window.scrollTo(0, 0);
          });
          await page.waitForTimeout(200);
          const path = `${output}-real-${width}-${theme}.png`;
          await page.screenshot({ path, animations: "disabled" });
          const geometry = await workspace.evaluate((node) => ({
            images: node.querySelectorAll("img").length,
            viewport: innerWidth,
            pageWidth: document.documentElement.scrollWidth,
            columns: [...node.querySelectorAll("[data-column-id]")].map((column) => {
              const scroll = column.querySelector('[role="region"][aria-label^="Articles in "]');
              return {
                kind: column.dataset.feedKind,
                card: column.getBoundingClientRect().toJSON(),
                innerWidth: column.clientWidth,
                header: column.firstElementChild.getBoundingClientRect().toJSON(),
                scroll: scroll.getBoundingClientRect().toJSON(),
                scrollWidth: scroll.scrollWidth,
                scrollHeight: scroll.scrollHeight,
                clientHeight: scroll.clientHeight,
              };
            }),
            firstRow: node.querySelector(".article-stacked-row")?.getBoundingClientRect().toJSON(),
          }));
          assert.equal(geometry.images, 0);
          assert.equal(geometry.pageWidth, width);
          for (const item of geometry.columns) {
            assert.equal(item.header.height, 76, "Every column header stays aligned");
            assert.ok(
              Math.abs(item.scroll.width - item.innerWidth) <= 1,
              "Private and public column bodies occupy their full width",
            );
            assert.ok(
              item.scrollWidth <= item.innerWidth + 1,
              "Article content fits the column body",
            );
            assert.ok(
              item.scroll.bottom <= item.card.bottom + 1,
              "Each feed scrolls inside its own card",
            );
            assert.ok(
              item.scrollHeight > item.clientHeight,
              "Long feeds retain their own vertical scroll surface",
            );
          }
          visual.push({ path, width, theme, ...geometry });
          console.log("Columns visual", path);
          if (width === 1440 && theme === "dark") {
            const menu = await openColumnMenu(page, recommended, { keyboard: true });
            await page.screenshot({ path: `${output}-real-options.png`, animations: "disabled" });
            await page.keyboard.press("Escape");
            await menu.waitFor({ state: "hidden" });
          }
        }
      await writeFile(
        `${output}-real.json`,
        JSON.stringify(
          {
            passed: true,
            visual,
            articles: body.items.slice(0, 12).map(({ title, summary }) => ({ title, summary })),
          },
          null,
          2,
        ),
      );
      return;
    }
    fixture.failNext("tag", { cursor: "" });
    fixture.pause();
    await seed(columnsFeedSelections);
    await wait(() => fixture.active === 3, "Three independent feed/selection requests start");
    await page.waitForTimeout(150);
    assert.equal(
      fixture.maximum,
      3,
      "The shared queue holds additional columns behind three requests",
    );
    fixture.resume();
    await Promise.all([latest, source, topic].map((locator) => waitRows(locator, 4)));
    await tag.getByRole("button", { name: "Try again", exact: true }).waitFor();
    if (signedIn) await waitRows(recommended, 4);
    else {
      await recommended.getByText("Sign in to view recommendations.", { exact: true }).waitFor();
      assert.equal(
        fixture.requests.filter(({ kind }) => kind === "recommended").length,
        0,
        "Guest Recommended never calls the private feed",
      );
    }
    assert.ok(fixture.maximum <= 3);
    const columnBodies = await workspace.locator("[data-column-id]").evaluateAll((nodes) =>
      nodes.map((node) => ({
        kind: node.dataset.feedKind,
        innerWidth: node.clientWidth,
        body: node
          .querySelector('[role="region"][aria-label^="Articles in "]')
          .getBoundingClientRect()
          .toJSON(),
        card: node.getBoundingClientRect().toJSON(),
        headerHeight: node.firstElementChild.getBoundingClientRect().height,
      })),
    );
    for (const item of columnBodies) {
      assert.ok(
        Math.abs(item.body.width - item.innerWidth) <= 1,
        `${item.kind} fills its column instead of shrinking`,
      );
      assert.equal(item.headerHeight, 76);
      assert.ok(item.body.bottom <= item.card.bottom + 1, `${item.kind} scrolls within its card`);
    }
    const readingGeometry = await latest.evaluate((node) => {
      const rows = [...node.querySelectorAll(".article-stacked-row")];
      const first = rows[0];
      const rect = (element) => element?.getBoundingClientRect().toJSON();
      return {
        column: rect(node),
        title: rect(first.querySelector(".article-stacked-title")),
        summary: rect(first.querySelector(".article-stacked-description")),
        images: node.querySelectorAll("img").length,
        thumbnails: node.querySelectorAll(".article-stacked-thumbnail").length,
        actions: [
          ...first.querySelectorAll(".article-quick-actions button,.article-quick-actions a"),
        ]
          .filter((element) => element.getClientRects().length)
          .map(rect),
        minimumActionSize: matchMedia("(pointer: coarse)").matches ? 44 : 32,
        minimumActionWidth: matchMedia("(pointer: coarse)").matches ? 44 : 24,
      };
    });
    assert.ok(readingGeometry.column.width >= 350, "Desktop columns provide room for reading");
    assert.ok(
      readingGeometry.title.width >= 280,
      "Image-free headlines use the available column width",
    );
    assert.equal(readingGeometry.images, 0, "Columns omit article and publisher images");
    assert.equal(readingGeometry.thumbnails, 0, "Columns reserve no thumbnail gutter");
    assert.ok(readingGeometry.summary.width >= 280, "Summaries use the available column width");
    assert.equal(
      fixture.requests.length > 0 && columnsFixtureArticle("columns-latest-0-3").image_url !== null,
      true,
      "Image suppression is exercised against articles that have managed images",
    );
    assert.ok(
      readingGeometry.actions.every(
        ({ width, height }) =>
          width >= readingGeometry.minimumActionWidth &&
          height >= readingGeometry.minimumActionSize,
      ),
      "Article actions retain pointer-appropriate hit targets",
    );
    assert.equal(await workspace.locator("img").count(), 0, "All Columns feeds are text only");
    if (["cold", "slow"].includes(previewDiagnostic)) {
      const states = [];
      const state = async (phase) => {
        states.push(
          await page.evaluate(
            (phase) => ({
              phase,
              url: location.href,
              workspace: document.querySelectorAll('[aria-label="Columns workspace"]').length,
              columns: [...document.querySelectorAll("[data-column-id]")].map(
                (node) => node.dataset.columnId,
              ),
              pageScroll: { x: scrollX, y: scrollY },
              boardScroll: document.querySelector('[aria-label="Column feeds"]')?.scrollLeft,
              columnScrolls: [...document.querySelectorAll("[data-column-id]")].map((node) => ({
                id: node.dataset.columnId,
                top: node.querySelector('[role="region"][aria-label^="Articles in "]')?.scrollTop,
              })),
              direct:
                document
                  .querySelector('[aria-label="Article preview"]')
                  ?.getAttribute("data-direct-entry") ?? null,
            }),
            phase,
          ),
        );
        await page.screenshot({
          path: `${output}-preview-${previewDiagnostic}-${phase}.png`,
          animations: "disabled",
        });
      };
      const delayedRsc = [];
      if (previewDiagnostic === "slow")
        await page.context().route(
          (url) =>
            url.pathname === `/articles/${columnsSharedArticle.slug}` &&
            url.searchParams.has("_rsc"),
          async (route) => {
            delayedRsc.push({ url: route.request().url(), started: Date.now() });
            await new Promise((resolve) => setTimeout(resolve, 12000));
            await route.continue().catch(() => {});
          },
        );
      for (const locator of [latest, source]) {
        await locator.getByRole("button", { name: "Load more articles", exact: true }).click();
        await waitRows(locator, 7);
      }
      await latest.evaluate((node) => {
        node.querySelector('[role="region"][aria-label^="Articles in "]').scrollTop = 60;
      });
      await source.evaluate((node) => {
        node.querySelector('[role="region"][aria-label^="Articles in "]').scrollTop = 80;
      });
      await recordStableRows();
      await recommended.evaluate((node) => {
        node.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "instant" });
        node.querySelector('[role="region"][aria-label^="Articles in "]').scrollTop = 20;
      });
      const previewTitle = recommended.locator(sharedSelector).locator(".article-stacked-title");
      // Reveal the whole target before measuring; clicking a clipped title scrolls its feed.
      await previewTitle.scrollIntoViewIfNeeded();
      await page.waitForTimeout(100);
      const before = fixture.requests.length;
      const documentsBefore = navigationRequests.filter(({ type }) => type === "document").length;
      await state("before");
      assert.ok(
        states[0].columnScrolls.some(({ top }) => top > 0),
        "An independent feed has a meaningful reading position",
      );
      await previewTitle.click();
      await page.waitForTimeout(11000);
      await state("11s");
      const modal = page.getByRole("dialog", { name: "Article preview", exact: true });
      await modal.waitFor();
      await state("open");
      assert.equal(
        await modal.getAttribute("data-direct-entry"),
        null,
        "A delayed preview preserves its intercepted entry",
      );
      assert.equal(await workspace.count(), 1, "A slow preview keeps My feed behind the modal");
      await modal.getByRole("button", { name: "Close preview", exact: true }).click();
      await modal.waitFor({ state: "hidden" });
      await page.waitForURL(target);
      await workspace.waitFor();
      await state("closed");
      await writeFile(
        `${output}-preview-${previewDiagnostic}.json`,
        JSON.stringify(
          {
            states,
            delayedRsc,
            navigationRequests,
            feedReadsBefore: before,
            feedReadsAfter: fixture.requests.length,
          },
          null,
          2,
        ),
      );
      assert.equal(await workspace.count(), 1);
      await assertStableRows();
      assert.equal(fixture.requests.length, before);
      assert.deepEqual(
        states.at(-1).pageScroll,
        states[0].pageScroll,
        "Closing restores the reading page position",
      );
      assert.equal(
        states.at(-1).boardScroll,
        states[0].boardScroll,
        "Closing retains horizontal column position",
      );
      assert.deepEqual(
        states.at(-1).columnScrolls,
        states[0].columnScrolls,
        "Closing retains each feed's vertical position",
      );
      assert.equal(
        navigationRequests.filter(({ type }) => type === "document").length,
        documentsBefore,
        "Slow preview transport never forces a document reload",
      );
      return;
    }
    const initial = fixture.requests.filter(({ cursor }) => !cursor);
    assert.ok(
      initial.every(({ limit }) => limit === 24),
      "Columns request a bounded 24 articles per page",
    );
    assert.equal(
      new URL(initial.find(({ kind }) => kind === "latest").url).searchParams.get("diverse"),
      "true",
    );
    assert.equal(
      new URL(initial.find(({ kind }) => kind === "source").url).searchParams.get("source_id"),
      columnsCatalog.sources[0].id,
    );
    assert.equal(
      new URL(initial.find(({ kind }) => kind === "topic").url).searchParams.get("topic"),
      columnsCatalog.topics[0].id,
    );
    assert.equal(
      new URL(initial.find(({ kind }) => kind === "tag").url).searchParams.get("tag"),
      columnsCatalog.tags[0].slug,
    );
    assert.equal(
      await latest.locator(sharedSelector).count(),
      1,
      "An article duplicated by the API appears once per column",
    );
    assert.equal(
      await source.locator(sharedSelector).count(),
      1,
      "The same article may appear in another matching column",
    );
    assert.ok(fixture.engagementReads.every((ids) => ids.length <= 100));
    assert.ok(
      fixture.engagementReads.flat().filter((id) => id === columnsSharedArticle.id).length <= 1,
      "Shared articles use the workspace engagement batcher",
    );

    await tag.getByRole("button", { name: "Try again", exact: true }).click();
    await waitRows(tag, 4);
    await recordStableRows();
    await latest.getByRole("button", { name: "Load more articles", exact: true }).click();
    await waitRows(latest, 7);
    assert.equal(await count(source), 4);
    assert.equal(
      fixture.requests.filter(({ kind, cursor }) => kind === "source" && cursor).length,
      0,
    );
    await source.getByRole("button", { name: "Load more articles", exact: true }).click();
    await waitRows(source, 7);
    assert.equal(
      fixture.requests.find(({ kind, cursor }) => kind === "latest" && cursor).cursor,
      "latest:1",
    );
    assert.equal(
      fixture.requests.find(({ kind, cursor }) => kind === "source" && cursor).cursor,
      "source:1",
    );
    await recordStableRows();
    fixture.failNext("topic", { cursor: "topic:1" });
    await topic.getByRole("button", { name: "Load more articles", exact: true }).click();
    await topic.getByRole("button", { name: "Try again", exact: true }).waitFor();
    await assertStableRows();
    assert.equal(await count(topic), 4, "Pagination failures retain this column's existing page");
    await topic.getByRole("button", { name: "Try again", exact: true }).click();
    await waitRows(topic, 7);
    await assertStableRows();

    const shared = workspace.locator(sharedSelector);
    if (signedIn) {
      await latest.locator(sharedSelector).locator(".bookmark-button").click();
      await wait(
        async () =>
          (await shared.locator('.bookmark-button[aria-pressed="true"]').count()) ===
          (await shared.count()),
        "Bookmarks update every matching column",
      );
      await source
        .locator(sharedSelector)
        .getByRole("button", { name: /^Like article/ })
        .click();
      await wait(
        async () =>
          (await shared.locator('.heart-button[aria-pressed="true"]').count()) ===
          (await shared.count()),
        "Likes update every matching column",
      );
    }
    const original = source.locator(sharedSelector).locator(".article-stacked-read");
    await original.evaluate((node) =>
      node.addEventListener("click", (event) => event.preventDefault(), { once: true }),
    );
    await original.click();
    await wait(
      async () =>
        (await shared
          .locator('.open-count[aria-label="1 clicks to the original article"]')
          .count()) === (await shared.count()),
      "Read counts update every matching column",
    );

    const feedReadCount = fixture.requests.length;
    const previewStates = [];
    const recordPreview = async (phase) => {
      previewStates.push(
        await page.evaluate(
          (phase) => ({
            phase,
            url: location.href,
            historyLength: history.length,
            historyKeys: Object.keys(history.state ?? {}),
            workspace: document.querySelectorAll('[aria-label="Columns workspace"]').length,
            columns: [...document.querySelectorAll("[data-column-id]")].map(
              (node) => node.dataset.columnId,
            ),
            rows: [...document.querySelectorAll(".article-stacked-row")].map(
              (node) => node.dataset.articleId,
            ),
            previewDirect: document
              .querySelector('[aria-label="Article preview"]')
              ?.getAttribute("data-direct-entry"),
          }),
          phase,
        ),
      );
      if (process.env.DEVFEED_COLUMNS_PREVIEW_DIAGNOSTIC)
        await page.screenshot({ path: `${output}-preview-${phase}.png`, animations: "disabled" });
    };
    await recordPreview("before");
    await source.locator(sharedSelector).locator(".article-stacked-title").click();
    const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
    await preview.waitFor();
    await recordPreview("open");
    if (process.env.DEVFEED_COLUMNS_PREVIEW_DIAGNOSTIC) {
      await page.waitForTimeout(11000);
      await recordPreview("open-11s");
    }
    assert.equal(
      await workspace.count(),
      1,
      "The My feed workspace stays mounted behind a preview",
    );
    await assertStableRows();
    await preview.getByRole("button", { name: "Next article", exact: true }).click();
    await preview.getByRole("heading", { name: "Source article 1.2", exact: true }).waitFor();
    await preview.getByRole("button", { name: "Close preview", exact: true }).click();
    await preview.waitFor({ state: "hidden" });
    await page.waitForURL(target);
    await recordPreview("closed");
    await assertStableRows();
    assert.equal(
      fixture.requests.length,
      feedReadCount,
      "Returning from a preview never reloads or paginates columns",
    );
    if (process.env.DEVFEED_COLUMNS_PREVIEW_DIAGNOSTIC) {
      await writeFile(
        `${output}-preview.json`,
        JSON.stringify(
          { passed: true, previewStates, navigationRequests, requests: fixture.requests },
          null,
          2,
        ),
      );
      return;
    }

    const late = fixture.holdNext("topic", { cursor: "topic:2" });
    await topic.getByRole("button", { name: "Load more articles", exact: true }).click();
    await wait(
      () => fixture.requests.some(({ kind, cursor }) => kind === "topic" && cursor === "topic:2"),
      "The old topic page is in flight",
    );
    await edit(topic, columnsCatalog.topics[0].name, "topic", columnsCatalog.topics[1]);
    await topic
      .getByRole("heading", { name: columnsCatalog.topics[1].name, exact: true })
      .waitFor();
    await waitRows(topic, 4);
    late.release();
    await wait(
      () =>
        fixture.requests
          .filter(({ kind, cursor }) => kind === "topic" && cursor === "topic:2")
          .every(({ finished }) => finished),
      "The replaced topic response settles",
    );
    assert.equal(
      await topic.getByText(/^Topic article/).count(),
      0,
      "A late response cannot populate a replacement selection",
    );
    await assertStableRows();
    const removed = fixture.holdNext("rust", { cursor: "rust:1" });
    await topic.getByRole("button", { name: "Load more articles", exact: true }).click();
    await wait(
      () => fixture.requests.some(({ kind, cursor }) => kind === "rust" && cursor === "rust:1"),
      "The removed column has a pending page",
    );
    await columnAction(page, topic, `Remove column: ${columnsCatalog.topics[1].name}`);
    removed.release();
    await wait(
      () =>
        fixture.requests
          .filter(({ kind, cursor }) => kind === "rust" && cursor === "rust:1")
          .every(({ finished }) => finished),
      "The removed column response settles",
    );
    assert.equal(await topic.count(), 0);
    await assertStableRows();

    fixture.setEmpty("tag", true);
    fixture.setRecommendation({ has_interests: false });
    await seed(columnsFeedSelections);
    await Promise.all([latest, source, topic].map((locator) => waitRows(locator, 4)));
    await tag.getByText("No articles yet.", { exact: true }).waitFor();
    await recommended
      .getByText("Follow sources or topics to get started.", { exact: true })
      .waitFor();
    await recommended.getByRole("link", { name: "Choose topics", exact: true }).waitFor();

    fixture.setEmpty("tag", false);
    fixture.setRecommendation({ has_interests: true, status: "refreshing" });
    await seed(columnsFeedSelections);
    await Promise.all([latest, source, topic, tag].map((locator) => waitRows(locator, 4)));
    await recommended
      .getByText("Your recommendations are being prepared.", { exact: true })
      .waitFor();
    await recordStableRows();
    fixture.setRecommendation({ status: "ready" });
    await recommended.getByRole("button", { name: "Check again", exact: true }).click();
    await waitRows(recommended, 4);
    await assertStableRows();
    fixture.failNext("recommended", { cursor: "recommended:1", status: 409 });
    await recommended.getByRole("button", { name: "Load more articles", exact: true }).click();
    await recommended.getByText("Your feed has been updated.", { exact: true }).waitFor();
    assert.equal(await count(recommended), 4, "An expired cursor retains its visible page");
    await assertStableRows();
    await recommended.getByRole("button", { name: "Show updated feed", exact: true }).click();
    await waitRows(recommended, 4);
    await assertStableRows();
    fixture.setEmpty("recommended", true);
    fixture.failNext("recommended", { cursor: "recommended:1", status: 409 });
    await recommended.getByRole("button", { name: "Load more articles", exact: true }).click();
    await recommended.getByRole("button", { name: "Show updated feed", exact: true }).click();
    await recommended.getByText("No recommendations yet.", { exact: true }).waitFor();
    await assertStableRows();
    fixture.setEmpty("recommended", false);
    await latest.evaluate((node) => {
      node.parentElement.scrollLeft = 0;
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    });
    await page.screenshot({ path: `${output}-independent-desktop.png`, animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 1000 });
    await source.evaluate((node) =>
      node.scrollIntoView({ block: "nearest", inline: "start", behavior: "instant" }),
    );
    await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: "instant" }));
    await page.screenshot({ path: `${output}-independent-mobile.png`, animations: "disabled" });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );

    fixture.setStress(true);
    fixture.setRecommendation({ has_interests: true, status: "ready" });
    await wait(() => fixture.active === 0, "Requests settle before the resource-bound scenario");
    fixture.resetMaximum();
    fixture.pause();
    const stressColumns = columnsStressSelections;
    assert.equal(stressColumns.length, 12);
    assert.equal(
      new Set(stressColumns.map(({ feed }) => `${feed.kind}:${feed.id ?? ""}`)).size,
      12,
      "The resource-bound scenario uses twelve different feeds",
    );
    const stressStart = fixture.requests.length;
    const stressMetadataStart = fixture.metadata.length;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await seed(stressColumns);
    await wait(() => fixture.active === 3, "Twelve columns share three request slots");
    await page.waitForTimeout(150);
    assert.equal(
      fixture.requests.length - stressStart + fixture.metadata.length - stressMetadataStart,
      3,
      "Remaining feed and selection requests stay behind three shared slots",
    );
    resources = await beginResourceProfile(page);
    await resources.sample("paused-before-articles");
    fixture.resume();
    await Promise.all(stressColumns.map(({ id }) => waitRows(column(id), 24)));
    await resources.sample("first-pages");
    for (const { id } of stressColumns) {
      for (const total of [48, 72]) {
        await column(id).getByRole("button", { name: "Load more articles", exact: true }).click();
        await waitRows(column(id), total);
      }
      await column(id)
        .getByText("Showing up to 72 articles in this column.", {
          exact: true,
        })
        .waitFor();
      assert.equal(
        await column(id).getByRole("button", { name: "Load more articles", exact: true }).count(),
        0,
      );
    }
    assert.equal(
      await workspace.locator(rowSelector).count(),
      864,
      "The workspace never renders more than twelve bounded feeds",
    );
    assert.equal(
      fixture.requests.length - stressStart,
      36,
      "Three retained pages per column bound requests and DOM growth",
    );
    for (const kind of [
      "latest",
      ...Array.from({ length: 11 }, (_, index) => `stress${index + 1}`),
    ])
      assert.equal(
        fixture.requests.slice(stressStart).filter((request) => request.kind === kind).length,
        3,
        "Every distinct selection retains its own three-page cursor sequence",
      );
    assert.ok(fixture.maximum <= 3, "Resource bounds remain enforced through all pagination");
    await resources.sample("retained-pages");
    await resources.cleanup();
    for (const [layout, image] of [
      ["Grid", ".article-card .card-image img"],
      ["Stacked", ".article-stacked-thumbnail img"],
    ]) {
      const settings = await openArticleViewSettings(page);
      await settings.getByRole("radio", { name: layout, exact: true }).check();
      await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
      await page.goto(target);
      await page.locator(image).first().waitFor();
      assert.equal(await workspace.count(), 0, `${layout} retains its ordinary image presentation`);
    }
    await writeFile(
      `${output}.json`,
      JSON.stringify(
        {
          passed: true,
          signedIn,
          maximum: fixture.maximum,
          readingGeometry,
          columnBodies,
          requests: fixture.requests,
          metadata: fixture.metadata,
          mutations: fixture.mutations,
          previewStates,
          engagementReads: fixture.engagementReads,
          resources: resources.proof,
        },
        null,
        2,
      ),
    );
  } catch (error) {
    await writeFile(
      `${output}-failure.json`,
      JSON.stringify(
        {
          error: String(error.stack ?? error),
          maximum: fixture.maximum,
          active: fixture.active,
          requests: fixture.requests,
          metadata: fixture.metadata,
          navigationRequests,
          resources: resources?.proof,
        },
        null,
        2,
      ),
    );
    await page
      .screenshot({ path: `${output}-failure.png`, animations: "disabled" })
      .catch(() => {});
    console.error("Columns feeds failure", page.url(), await page.locator("body").innerText());
    throw error;
  } finally {
    await resources?.cleanup();
    if (visualTouchSession) {
      await visualTouchSession
        .send("Emulation.setTouchEmulationEnabled", { enabled: false })
        .catch(() => {});
      await visualTouchSession.detach().catch(() => {});
    }
    page.off("request", recordRequest);
    await fixture.cleanup();
  }
}

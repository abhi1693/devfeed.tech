import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { openArticleViewSettings, setArticleView } from "./article-view.mjs";

export const stackedReaderTitle =
  "Engineering boundaries — 日本語, العربية, café, 👩🏽‍💻: " +
  "a dependable approach to application architecture and international collaboration "
    .repeat(3)
    .trim();
export const stackedReaderDescription =
  "A practical overview with Unicode 日本語 and an unusually long identifier " +
  "uninterrupted".repeat(35) +
  ". " +
  "Learn how teams build useful tools without losing the details that matter. ".repeat(9).trim();

export function stackedReaderArticle(article, index = 0) {
  return {
    ...article,
    id: index ? `33333333-3333-4333-8333-${String(index).padStart(12, "0")}` : article.id,
    slug: index ? `stacked-reader-${index}` : article.slug,
    title: index === 0 ? stackedReaderTitle : `Stacked article ${index + 1}`,
    ai_description: index === 0 ? stackedReaderDescription : null,
    summary: index === 1 ? "" : "A concise source excerpt about dependable engineering.",
    ai_summary: index === 1 ? null : "A useful AI overview of engineering boundaries.",
    image_url: index === 1 ? null : article.image_url,
    image_variants: index === 1 ? [] : article.image_variants,
    sources: index === 1 ? [] : article.sources,
    topics: index === 1 ? [] : article.topics,
    tags: index === 1 ? [] : article.tags,
    author: index === 1 ? null : article.author,
    published_at: index === 1 ? null : "2020-02-29T23:59:59Z",
    feed_at: "2026-10-21T00:00:00Z",
    canonical_url:
      index === 1 ? "https://fallback-publisher.example.test/article" : article.canonical_url,
  };
}

export function stackedReaderFeed(article, params) {
  const next = params.has("cursor");
  return {
    items: next
      ? [stackedReaderArticle(article, 12)]
      : Array.from({ length: 12 }, (_, index) => stackedReaderArticle(article, index)),
    next_cursor: next ? null : "stacked-next-page",
  };
}

async function checkStackedGeometry(page, width) {
  await page.setViewportSize({ width, height: 1000 });
  await page.evaluate(() => window.scrollTo(0, 0));
  const snapshot = await page
    .locator(".article-stacked")
    .first()
    .evaluate((container) => {
      const rows = [...container.querySelectorAll(".article-stacked-row")];
      const box = (element) => {
        const bounds = element.getBoundingClientRect();
        return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
      };
      const style = (element) => {
        const computed = getComputedStyle(element);
        return {
          fontSize: parseFloat(computed.fontSize),
          lineHeight: parseFloat(computed.lineHeight),
        };
      };
      return {
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        container: box(container),
        rows: rows.slice(0, 2).map((row) => ({
          bounds: box(row),
          title: box(row.querySelector(".article-stacked-title")),
          titleStyle: style(row.querySelector(".article-stacked-title")),
          metaStyle: style(row.querySelector(".article-stacked-meta")),
          description: row.querySelector(".article-stacked-description")
            ? box(row.querySelector(".article-stacked-description"))
            : null,
          actions: box(row.querySelector(".article-stacked-actions")),
          thumbnail: box(row.querySelector(".article-stacked-thumbnail")),
          divider: getComputedStyle(row).borderTopWidth,
        })),
      };
    });
  assert.ok(snapshot.documentWidth <= width + 1, `${width}px Stacked rows fit without overflow`);
  assert.ok(
    snapshot.container.x >= -1 && snapshot.container.x + snapshot.container.width <= width + 1,
  );
  assert.equal(snapshot.rows.length, 2);
  for (const row of snapshot.rows) {
    assert.ok(Math.abs(row.bounds.width - snapshot.container.width) <= 2);
    assert.ok(
      row.titleStyle.fontSize > row.metaStyle.fontSize,
      "Titles lead the metadata hierarchy",
    );
    assert.ok(row.thumbnail.width > 0 && row.thumbnail.height > 0);
    assert.ok(row.title.x >= row.bounds.x - 1 && row.title.x + row.title.width <= width + 1);
    assert.ok(row.actions.x >= row.bounds.x - 1 && row.actions.x + row.actions.width <= width + 1);
  }
  assert.ok(
    snapshot.rows[1].bounds.y >= snapshot.rows[0].bounds.y + snapshot.rows[0].bounds.height - 1,
  );
  assert.ok(parseFloat(snapshot.rows[1].divider) > 0, "Stacked rows have a shared divider");
  assert.ok(snapshot.rows[0].description.height > 0, "Long descriptions remain visible");
  assert.equal(snapshot.rows[1].description, null, "Missing descriptions leave no empty paragraph");
  return snapshot;
}

async function checkStackedTouchTargets(page, output) {
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await cdp.send("Emulation.setEmitTouchEventsForMouse", {
      enabled: true,
      configuration: "mobile",
    });
    await page.waitForFunction(() => matchMedia("(pointer: coarse)").matches);
    const geometry = await checkStackedGeometry(page, 320);
    const targets = await page
      .locator(".article-stacked-row")
      .first()
      .locator(".article-stacked-actions a, .article-stacked-actions button")
      .evaluateAll((elements) =>
        elements.map((element) => {
          const bounds = element.getBoundingClientRect();
          return {
            name: element.getAttribute("aria-label") ?? element.textContent,
            width: bounds.width,
            height: bounds.height,
          };
        }),
      );
    assert.ok(targets.length >= 4, "The touch measurement includes all available row actions");
    for (const target of targets)
      assert.ok(
        target.width >= 44 && target.height >= 44,
        `${target.name} has a 44px touch target`,
      );
    if (output) await page.screenshot({ path: `${output}-320-touch.png`, animations: "disabled" });
    return { pointer: "coarse", geometry, targets };
  } finally {
    await cdp.send("Emulation.setEmitTouchEventsForMouse", { enabled: false });
    await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
    await cdp.detach();
  }
}

/** Run against the actual website and both built extension readers. */
export async function checkStackedReader(page, target, output, { signedIn = false } = {}) {
  const originalViewport = page.viewportSize();
  const firstRow = page.locator(".article-stacked-row:visible").first();
  await page.goto(target);
  await setArticleView(page, "stacked", { returnTo: target, keyboard: true });
  await firstRow.waitFor();
  assert.equal(page.url(), target, "Stacked selection retains the requested feed filters");
  assert.equal(await page.locator(".article-card:visible, .article-list-row:visible").count(), 0);
  assert.equal(
    await page.getByRole("region", { name: "Articles in stacked view", exact: true }).count(),
    1,
  );
  assert.equal(await firstRow.locator(".article-stacked-title").textContent(), stackedReaderTitle);
  assert.equal(
    await firstRow.locator(".article-stacked-description").textContent(),
    stackedReaderDescription,
  );
  const second = page.locator(".article-stacked-row").nth(1);
  assert.match(await second.textContent(), /fallback-publisher\.example\.test/);
  assert.equal(await second.locator(".image-placeholder").count(), 1);
  assert.equal(await second.locator(".article-stacked-summary").count(), 0);
  assert.equal(await second.locator(".article-stacked-topic").count(), 0);
  await page.waitForFunction(() => {
    const image = document.querySelector(".article-stacked-thumbnail img");
    return image?.complete && image.naturalWidth > 0;
  });
  const measurements = [];
  if (output) await mkdir(dirname(output), { recursive: true });
  for (const width of [320, 768, 1440]) {
    measurements.push(await checkStackedGeometry(page, width));
    if (output) await page.screenshot({ path: `${output}-${width}.png`, animations: "disabled" });
  }
  measurements.push(await checkStackedTouchTargets(page, output));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await firstRow.waitFor();
  assert.equal(page.url(), target);
  if (!signedIn)
    assert.equal(
      await page.evaluate(
        () => JSON.parse(localStorage.getItem("devfeed:reader-preferences:guest")).view,
      ),
      "stacked",
      "Guest Stacked selection survives a reload",
    );
  await openArticleViewSettings(page);
  assert.equal(await page.getByRole("radio", { name: "Stacked", exact: true }).isChecked(), true);
  await page.goto(target);
  await firstRow.waitFor();
  const title = firstRow.locator(".article-stacked-title");
  const articleHref = await title.getAttribute("href");
  const bookmark = firstRow.locator(".bookmark-button");
  if (signedIn) {
    await bookmark.waitFor();
    const initial = await bookmark.getAttribute("aria-pressed");
    await bookmark.click();
    await firstRow
      .locator(`.bookmark-button[aria-pressed="${initial === "true" ? "false" : "true"}"]`)
      .waitFor();
  }
  await page.keyboard.press("Tab");
  await title.focus();
  assert.equal(await title.evaluate((element) => element.matches(":focus-visible")), true);
  await page.keyboard.press("Enter");
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  await preview.waitFor();
  await preview.getByRole("heading", { name: "AI overview", exact: true }).waitFor();
  await page.locator("dialog.article-modal:modal").waitFor();
  await page.locator(".truncated-text-tooltip:visible").waitFor({ state: "hidden" });
  await page.keyboard.press("Escape");
  await preview.waitFor({ state: "hidden" });
  await page.waitForURL(target);
  assert.equal(await title.evaluate((element) => element === document.activeElement), true);
  await firstRow.locator(".article-stacked-summary").click();
  await preview.waitFor();
  await preview.getByRole("heading", { name: "AI overview", exact: true }).waitFor();
  await preview.getByRole("button", { name: "Close preview", exact: true }).click();
  await preview.waitFor({ state: "hidden" });
  await page.waitForURL(target);
  assert.equal(await title.getAttribute("href"), articleHref);
  const share = firstRow.getByRole("button", { name: /^Share article:/ });
  await share.focus();
  await page.keyboard.press("Enter");
  const popover = page.getByRole("dialog", { name: "Share article", exact: true });
  await popover.waitFor();
  const sharedLink = await popover
    .getByRole("link", { name: "Share on X (opens in a new tab)", exact: true })
    .getAttribute("href");
  const publicUrl = new URL(sharedLink).searchParams.get("url");
  const destination = new URL(articleHref, "https://devfeed.tech");
  const articlePath = destination.hash.startsWith("#/")
    ? destination.hash.slice(1).split("?")[0]
    : destination.pathname;
  assert.equal(new URL(publicUrl).pathname, articlePath);
  await page.keyboard.press("Escape");
  await popover.waitFor({ state: "hidden" });
  assert.equal(await share.evaluate((element) => element === document.activeElement), true);
  const beforePagination = await page.locator(".article-stacked-row").count();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.locator('.article-stacked-title[href$="/articles/stacked-reader-12"]').waitFor();
  assert.equal(await page.locator(".article-stacked-row").count(), 13);
  assert.ok(beforePagination <= 13);
  assert.equal(page.url(), target, "Appending another page keeps the filtered feed route");
  await page.getByRole("combobox", { name: "Sort by", exact: true }).click();
  await page.getByRole("option", { name: "Newest", exact: true }).click();
  await page.waitForURL(
    (url) =>
      new URL(
        url.hash.startsWith("#/") ? url.hash.slice(1) : url.href,
        "https://devfeed.tech",
      ).searchParams.get("sort") === "newest",
  );
  await firstRow.waitFor();
  assert.equal(await page.locator(".article-card:visible, .article-list-row:visible").count(), 0);
  await page.getByRole("combobox", { name: "Sort by", exact: true }).click();
  await page.getByRole("option", { name: "Most liked", exact: true }).click();
  await page.waitForURL(target);
  await firstRow.waitFor();
  await setArticleView(page, "compact", { returnTo: target });
  const compactBookmark = page.locator(".article-list-row").first().locator(".bookmark-button");
  await compactBookmark.waitFor();
  if (signedIn)
    await page
      .locator('.article-list-row:first-child .bookmark-button[aria-pressed="true"]')
      .waitFor();
  await setArticleView(page, "stacked", { returnTo: target });
  await firstRow.waitFor();
  if (signedIn) await firstRow.locator('.bookmark-button[aria-pressed="true"]').waitFor();
  if (output)
    await writeFile(
      `${output}.json`,
      JSON.stringify({ passed: true, signedIn, measurements }, null, 2),
    );
  if (originalViewport) await page.setViewportSize(originalViewport);
}

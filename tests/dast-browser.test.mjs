import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { chromium } from "playwright";
import { pageContentSelector } from "../scripts/testing/dast-selectors.mjs";

let browser;
before(async () => {
  browser = await chromium.launch({ headless: true });
});
after(async () => {
  await browser?.close();
});

test("scans rendered admin content while the loading landmark is still present", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      '<main role="status">Loading administration</main><main id="main"><h1>Articles</h1></main>',
    );
    assert.equal(await page.locator("main").count(), 2);
    const content = page.locator(pageContentSelector("/content/articles"));
    await content.waitFor({ timeout: 1000 });
    assert.equal(await content.innerText(), "Articles");
  } finally {
    await page.close();
  }
});

test("scans the requested article while its background feed is hidden", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      '<main id="main" hidden>Background feed</main><dialog class="article-modal"><h1 id="article-preview-title">Requested article</h1></dialog>',
    );
    await page.locator("dialog").evaluate((dialog) => dialog.showModal());
    assert.equal(await page.locator("main").isVisible(), false);
    const content = page.locator(pageContentSelector("/articles/requested-article"));
    await content.waitFor({ timeout: 1000 });
    assert.equal(await content.innerText(), "Requested article");
  } finally {
    await page.close();
  }
});

test("a loading shell alone does not count as rendered application content", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<main role="status">Loading administration</main>');
    await assert.rejects(
      page.locator(pageContentSelector("/users")).waitFor({ timeout: 100 }),
      (error) => error.name === "TimeoutError",
    );
  } finally {
    await page.close();
  }
});

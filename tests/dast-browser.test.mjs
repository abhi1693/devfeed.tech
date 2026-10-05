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

test("waits for streamed settings content while duplicate loading landmarks exist", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      '<main id="main"><div class="loading-skeleton">Loading topics</div></main><div hidden><main id="main"><h1>Your topics</h1><input type="search"></main></div>',
    );
    assert.equal(await page.locator("main#main").count(), 2);
    const content = page.locator(pageContentSelector("/settings/topics"));
    assert.equal(await content.count(), 0);
    await page.evaluate(() => {
      setTimeout(() => {
        document.querySelector(".loading-skeleton").closest("main").remove();
        document.querySelector("[hidden]").hidden = false;
      }, 50);
    });
    await content.waitFor({ timeout: 1000 });
    assert.equal(await content.innerText(), "Your topics");
  } finally {
    await page.close();
  }
});

test("a visible settings skeleton does not count as rendered application content", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      '<main id="main"><h1>Settings</h1><section><div class="loading-skeleton">Loading topics</div></section></main>',
    );
    await assert.rejects(
      page.locator(pageContentSelector("/settings/topics")).waitFor({ timeout: 100 }),
      (error) => error.name === "TimeoutError",
    );
  } finally {
    await page.close();
  }
});

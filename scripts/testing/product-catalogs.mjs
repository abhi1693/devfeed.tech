import assert from "node:assert/strict";
import { chromium } from "playwright";

const [origin, product, titles] = process.argv.slice(2);
const catalog = JSON.parse(titles);
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  for (const route of ["/latest", "/search?q=Tracing"]) {
    await page.goto(origin + route);
    await page.getByText(catalog[product], { exact: true }).first().waitFor();
    for (const [other, title] of Object.entries(catalog)) {
      if (other !== product) assert.equal(await page.getByText(title, { exact: true }).count(), 0);
    }
  }
} finally {
  await browser.close();
}

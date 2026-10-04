import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "./accessibility.mjs";

test("the accessibility gate rejects contrast, missing names and invalid ARIA", async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const document = (content) =>
      `<!doctype html><html lang="en"><head><title>Accessibility gate fixture</title></head><body><main><h1>Reader controls</h1>${content}</main></body></html>`;
    await page.setContent(
      document(
        '<button aria-expanded="false">Feed filters</button><p style="color:#111;background:#fff">Readable text</p>',
      ),
    );
    assertAccessible(await new AxeBuilder({ page }).analyze(), "valid fixture");
    for (const [name, content, rule] of [
      ["contrast", '<p style="color:#bbb;background:#fff">Unreadable text</p>', "color-contrast"],
      ["name", '<button><svg aria-hidden="true"></svg></button>', "button-name"],
      ["ARIA", '<button aria-expanded="invalid">Feed filters</button>', "aria-valid-attr-value"],
    ]) {
      await page.setContent(document(content));
      const result = await new AxeBuilder({ page }).analyze();
      assert.ok(
        result.violations.some((violation) => violation.id === rule),
        `${name} regression is detected`,
      );
      assert.throws(() => assertAccessible(result, name), /Accessibility violations/);
    }
  } finally {
    await browser.close();
  }
});

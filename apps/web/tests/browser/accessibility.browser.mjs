import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { assertAccessible } from "./accessibility.mjs";
import { observeAnimations } from "./animation-recorder.mjs";

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

test("motion observations survive navigation and flush browser callbacks", async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const animations = await observeAnimations(page, async () => {
      for (let index = 0; index < 2; index++) {
        await page.evaluate(() => {
          const target = document.createElement("div");
          target.className = "motion-target";
          document.body.append(target);
          target.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 10 });
        });
        await page.reload();
      }
    });
    assert.deepEqual(animations, ["motion-target", "motion-target"]);
    assert.deepEqual(await observeAnimations(page, () => page.reload()), []);
  } finally {
    await browser.close();
  }
});

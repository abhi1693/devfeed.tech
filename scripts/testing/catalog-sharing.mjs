import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

export async function checkCatalogSharing(page, pathFor, collections, output, publicOrigin) {
  await mkdir(output, { recursive: true });
  for (const { kind, path } of collections) {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(pathFor(path));
    const heading = page.locator(`.${kind}-feed-heading`);
    const share = heading.getByRole("button", { name: new RegExp(`^Share ${kind}:`) });
    await share.waitFor();
    const follow = heading.locator(".follow-button");
    await follow.waitFor();
    await page.waitForFunction((kind) => {
      const actions = document.querySelector(`.${kind}-feed-heading .feed-heading-actions`);
      const follow = actions?.querySelector(".follow-button")?.getBoundingClientRect();
      const share = actions?.querySelector(".article-share-trigger")?.getBoundingClientRect();
      return follow?.width && share?.width && share.right <= follow.x;
    }, kind);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (text) => {
            window.__devfeedSharedLink = text;
          },
        },
      });
    });
    await share.click();
    const panel = page.getByRole("dialog", { name: `Share ${kind}`, exact: true });
    await panel.waitFor();
    const canonical = new URL(path, publicOrigin).href;
    for (const link of await panel.getByRole("link").all()) {
      assert.equal(new URL(await link.getAttribute("href")).searchParams.get("url"), canonical);
    }
    await panel.getByRole("button", { name: "Copy link", exact: true }).click();
    await panel.getByText("Link copied.", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.__devfeedSharedLink), canonical);
    await page.screenshot({ path: `${output}/${kind}-desktop.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
    await panel.waitFor({ state: "hidden" });
    await page.waitForFunction(
      (button) => button === document.activeElement,
      await share.elementHandle(),
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await share.click();
    await panel.waitFor();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    const bounds = await panel.boundingBox();
    assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390, "Share menu fits mobile");
    await page.screenshot({ path: `${output}/${kind}-mobile.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
  }
}

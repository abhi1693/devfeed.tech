import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";
import { touchReaderTarget } from "../../web/tests/browser/reader-interactions.mjs";

for (const replaceAfterPreflight of [false, true]) {
  test(
    replaceAfterPreflight
      ? "reader touch retries bounds when the target remounts after preflight"
      : "reader touch re-resolves a target replaced during scrolling",
    async () => {
      const browser = await chromium.launch({
        executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? undefined
          : process.env.DEVFEED_EXTENSION_BROWSER === "edge"
            ? "msedge"
            : "chromium",
        headless: true,
      });
      try {
        const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
        const cdp = await page.context().newCDPSession(page);
        await cdp.send("Emulation.setTouchEmulationEnabled", {
          enabled: true,
          maxTouchPoints: 1,
        });
        await page.setContent(`
      <style>button { margin-top: 1500px; width: 180px; height: 60px; }</style>
      <button id="target">Open article</button>
      <script>
        window.result = { replacements: 0, touches: 0, clicks: 0 };
        const target = document.querySelector("button");
        window.replaceTarget = () => {
          const current = document.querySelector("button");
          const replacement = current.cloneNode(true);
          replacement.addEventListener("touchstart", () => window.result.touches++);
          replacement.addEventListener("click", () => window.result.clicks++);
          current.replaceWith(replacement);
          window.result.replacements++;
        };
        const observer = new IntersectionObserver(([entry]) => {
          if (!entry.isIntersecting) return;
          observer.disconnect();
          window.replaceTarget();
        });
        observer.observe(target);
      </script>
    `);
        const target = page.getByRole("button", { name: "Open article" });
        let boundsReads = 0;
        const locator = replaceAfterPreflight
          ? {
              click: (options) => target.click(options),
              boundingBox: async () => {
                boundsReads++;
                if (boundsReads === 1) {
                  await page.waitForFunction(() => window.result.replacements === 1);
                  const previous = await target.elementHandle();
                  await page.evaluate(() => window.replaceTarget());
                  const bounds = await previous.boundingBox();
                  await previous.dispose();
                  assert.equal(bounds, null, "the remounted target has detached bounds");
                  return bounds;
                }
                return target.boundingBox();
              },
            }
          : target;
        await touchReaderTarget(cdp, locator);
        if (replaceAfterPreflight) assert.ok(boundsReads >= 2);
        await page.waitForFunction(() => window.result.clicks === 1);
        assert.deepEqual(await page.evaluate(() => window.result), {
          replacements: replaceAfterPreflight ? 2 : 1,
          touches: 1,
          clicks: 1,
        });
      } finally {
        await browser.close();
      }
    },
  );
}

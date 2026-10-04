import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";
import { touchReaderTarget } from "../../web/tests/browser/reader-interactions.mjs";

test("reader touch re-resolves a target replaced during scrolling", async () => {
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
        const observer = new IntersectionObserver(([entry]) => {
          if (!entry.isIntersecting) return;
          observer.disconnect();
          const replacement = target.cloneNode(true);
          replacement.addEventListener("touchstart", () => window.result.touches++);
          replacement.addEventListener("click", () => window.result.clicks++);
          target.replaceWith(replacement);
          window.result.replacements++;
        });
        observer.observe(target);
      </script>
    `);
    await touchReaderTarget(cdp, page.getByRole("button", { name: "Open article" }));
    await page.waitForFunction(() => window.result.clicks === 1);
    assert.deepEqual(await page.evaluate(() => window.result), {
      replacements: 1,
      touches: 1,
      clicks: 1,
    });
  } finally {
    await browser.close();
  }
});

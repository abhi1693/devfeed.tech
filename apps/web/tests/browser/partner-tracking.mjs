import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "esbuild";
import { chromium } from "playwright";

// Controlled placements exercise the infrastructure before any placement is launched.
// Backend integration tests separately verify signed receipts and durable counters.
const root = await mkdtemp(path.join(tmpdir(), "devfeed-tracking-browser-"));
const html =
  '<!doctype html><html><body style="margin:0;height:300vh"><a id="placement" href="#destination" style="display:block;margin-top:180vh;height:100px">Fixture placement</a><script src="./tracking-fixture.js"></script></body></html>';
try {
  for (const platform of ["web", "chrome", "edge"]) {
    const receipt = `controlled-${platform}-delivery`;
    const compiled = await build({
      stdin: {
        contents: `
          import { observePartnerPlacement } from "./apps/web/src/lib/partner-tracking";
          import { configureReaderRuntime } from "./apps/web/src/lib/reader-runtime";
          import { createReaderTransport } from "./apps/extensions/src/transport";
          if (location.protocol === "chrome-extension:") {
            configureReaderRuntime({ request: createReaderTransport(fetch), publicOrigin: "https://devfeed.tech" });
          }
          observePartnerPlacement(document.querySelector("#placement"), ${JSON.stringify(receipt)});
        `,
        resolveDir: process.cwd(),
        sourcefile: "tracking-fixture.ts",
      },
      bundle: true,
      write: false,
      format: "iife",
      platform: "browser",
    });
    const script = compiled.outputFiles[0].text;
    let fixture;
    let extensionId;
    if (platform !== "web") {
      fixture = path.join(root, `${platform}-extension`);
      await cp(path.resolve(`apps/extensions/dist/${platform}`), fixture, { recursive: true });
      const manifest = JSON.parse(await readFile(path.join(fixture, "manifest.json"), "utf8"));
      extensionId = createHash("sha256")
        .update(Buffer.from(manifest.key, "base64"))
        .digest("hex")
        .slice(0, 32)
        .replace(/[0-9a-f]/g, (digit) => String.fromCharCode(97 + parseInt(digit, 16)));
      await writeFile(path.join(fixture, "tracking-fixture.html"), html);
      await writeFile(path.join(fixture, "tracking-fixture.js"), script);
    }
    const context = await chromium.launchPersistentContext(path.join(root, `${platform}-profile`), {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
      channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
        ? undefined
        : platform === "edge"
          ? "msedge"
          : "chromium",
      headless: true,
      viewport: { width: 1280, height: 800 },
      args: fixture
        ? [`--disable-extensions-except=${fixture}`, `--load-extension=${fixture}`]
        : [],
    });
    try {
      const events = [];
      await context.addCookies([
        {
          name: "devfeed_user_session",
          value: "private-fixture",
          domain: "devfeed.tech",
          path: "/",
          secure: true,
          httpOnly: true,
        },
      ]);
      await context.route("https://devfeed.tech/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.pathname === "/api/v1/partner-tracking/events") {
          assert.equal(request.method(), "POST");
          assert.equal(request.headers().cookie, undefined);
          assert.equal(request.headers().authorization, undefined);
          assert.equal(request.headers().referer, undefined);
          const event = request.postDataJSON();
          assert.equal(event.receipt, receipt);
          assert.deepEqual(Object.keys(event).sort(), ["kind", "receipt"]);
          events.push(event.kind);
          await route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*" } });
        } else if (url.pathname.endsWith("tracking-fixture.js")) {
          await route.fulfill({ contentType: "text/javascript", body: script });
        } else {
          await route.fulfill({ contentType: "text/html", body: html });
        }
      });
      const page = await context.newPage();
      await page.addInitScript(() =>
        Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false }),
      );
      await page.goto(
        platform === "web"
          ? "https://devfeed.tech/tracking-fixture.html"
          : `chrome-extension://${extensionId}/tracking-fixture.html`,
      );
      await page.waitForTimeout(1200);
      assert.deepEqual(events, [], `${platform}: offscreen placements do not count`);
      // Headless tabs can remain visible when another page is foregrounded.
      // Drive visibility deterministically while keeping real intersection timing.
      await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await page.locator("#placement").scrollIntoViewIfNeeded();
      await page.waitForTimeout(1200);
      assert.deepEqual(events, [], `${platform}: hidden placements do not count`);
      await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "visible",
        });
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await page.waitForTimeout(1300);
      assert.deepEqual(
        events,
        ["impression"],
        `${platform}: visible unfocused placement counts once`,
      );
      await page.locator("#placement").click();
      await page.locator("#placement").click();
      await page.waitForTimeout(300);
      assert.deepEqual(
        events,
        ["impression", "click"],
        `${platform}: click is independent and deduplicated`,
      );
      console.log(
        `${platform}: offscreen/hidden filtering, viewable impression, independent click, deduplication and credential isolation passed`,
      );
    } finally {
      await context.close();
    }
  }
} finally {
  await rm(root, { recursive: true, force: true });
}

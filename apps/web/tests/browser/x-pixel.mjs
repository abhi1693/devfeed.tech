import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve(import.meta.dirname, "../../../..");
const browser = await chromium.launch({ headless: true });
try {
  for (const flag of [undefined, "false", "true"]) {
    const probe = createServer();
    await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
    const port = probe.address().port;
    await new Promise((resolve) => probe.close(resolve));
    const origin = `http://127.0.0.1:${port}`;
    const env = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
    );
    const app = spawn(
      process.execPath,
      [
        path.join(root, "node_modules/next/dist/bin/next"),
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        String(port),
      ],
      {
        cwd: path.join(root, "apps/web"),
        env: {
          ...env,
          DEVFEED_USER_BASE_URL: origin,
          DEVFEED_USER_API_URL: "http://127.0.0.1:9",
          DEVFEED_MCP_PUBLIC_URL: "https://mcp.example.com/mcp",
          DEVFEED_ANALYTICS_ENABLED: "false",
          ...(flag === undefined ? {} : { DEVFEED_X_PIXEL_ENABLED: flag }),
        },
        stdio: "pipe",
      },
    );
    let logs = "";
    app.stdout.on("data", (data) => (logs += data));
    app.stderr.on("data", (data) => (logs += data));
    const context = await browser.newContext();
    let loaders = 0;
    try {
      for (let attempt = 0; attempt < 100; attempt++) {
        try {
          await fetch(`${origin}/legal/privacy`);
          break;
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }
      await context.route("**/api/v1/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        const body = pathname.endsWith("/auth/me")
          ? null
          : pathname.endsWith("/auth/config")
            ? { enabled: false, providers: [] }
            : {};
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      });
      await context.route("https://static.ads-twitter.com/uwt.js", (route) => {
        loaders += 1;
        return route.fulfill({
          contentType: "application/javascript",
          body: `window.__xPixelCalls = window.twq.queue.map((args) => Array.from(args));
window.twq.exe = function (...args) { window.__xPixelCalls.push(args); };
window.twq.queue = [];
fetch('https://analytics.twitter.com/i/adsct', { method: 'POST', body: 'pixel-test' });`,
        });
      });
      await context.route("https://analytics.twitter.com/**", (route) =>
        route.fulfill({
          contentType: "application/json",
          headers: { "Access-Control-Allow-Origin": "*" },
          body: "{}",
        }),
      );
      await context.addInitScript(() => {
        window.__cspViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => {
          window.__cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
        });
      });
      const page = await context.newPage();
      const response = await page.goto(`${origin}/legal/privacy`);
      assert.equal(response.status(), 200);
      const html = await response.text();
      assert.equal(html.includes("twq('config','pc5f8')"), flag === "true");
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .waitFor();
      if (flag === "true") {
        await page.waitForFunction(() => window.__xPixelCalls?.length === 1);
        assert.deepEqual(await page.evaluate(() => window.__xPixelCalls), [["config", "pc5f8"]]);
        assert.equal(await page.locator("#x-pixel").count(), 1);
        assert.match(await page.locator("#x-pixel").textContent(), /twq\('config','pc5f8'\)/);
      }
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .click();
      await page.getByRole("heading", { name: "Connect your agent", exact: true }).waitFor();
      if (flag === "true") {
        assert.deepEqual(await page.evaluate(() => window.__xPixelCalls), [["config", "pc5f8"]]);
        assert.equal(loaders, 1, "Client navigation keeps the same initialized pixel");
      } else {
        assert.equal(await page.locator("#x-pixel").count(), 0);
        assert.equal(await page.evaluate(() => typeof window.twq), "undefined");
        assert.equal(loaders, 0);
      }
      assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
      console.log(`X pixel browser check passed: flag=${flag ?? "unset"}`);
    } catch (error) {
      console.error(logs);
      throw error;
    } finally {
      await context.close();
      app.kill("SIGTERM");
      await new Promise((resolve) => app.once("exit", resolve));
    }
  }
} finally {
  await browser.close();
}

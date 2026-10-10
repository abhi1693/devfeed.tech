import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve(import.meta.dirname, "../../../..");
const browser = await chromium.launch({
  headless: true,
  args: ["--test-third-party-cookie-phaseout"],
});
try {
  for (const flag of [undefined, "false", "true"]) {
    const probe = createServer();
    await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
    const port = probe.address().port;
    await new Promise((resolve) => probe.close(resolve));
    const origin = `http://127.0.0.1:${port}`;
    const clickId = "ad-click-1";
    let loginCookies = "";
    const userApi = createServer((request, response) => {
      if (request.url.startsWith("/v1/user/auth/login")) {
        loginCookies = request.headers.cookie ?? "";
        response.writeHead(302, { Location: `${origin}/legal/privacy` });
        response.end();
      } else {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end("{}");
      }
    });
    await new Promise((resolve) => userApi.listen(0, "127.0.0.1", resolve));
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
          DEVFEED_USER_API_URL: `http://127.0.0.1:${userApi.address().port}`,
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
        if (pathname.endsWith("/auth/login")) return route.continue();
        const body = pathname.endsWith("/auth/me")
          ? null
          : pathname.endsWith("/auth/config")
            ? { enabled: false, providers: [] }
            : {};
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      });
      const page = await context.newPage();
      const cdp = await context.newCDPSession(page);
      await cdp.send("Network.enable");
      await cdp.send("Network.setCookieControls", {
        enableThirdPartyCookieRestriction: true,
        disableThirdPartyCookieMetadata: true,
        disableThirdPartyCookieHeuristics: true,
      });
      await page.goto(`${origin}/legal/privacy`);
      await page.getByRole("heading", { name: "Privacy Policy", exact: true }).waitFor();
      assert.equal(
        (await context.cookies(origin)).some((cookie) => cookie.name.includes("x_click")),
        false,
      );
      const response = await page.goto(`${origin}/legal/privacy?twclid=${clickId}`);
      assert.equal(response.status(), 200);
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .waitFor();
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .click();
      await page.getByRole("heading", { name: "Connect your agent", exact: true }).waitFor();
      const cookie = (await context.cookies(origin)).find(
        (item) => item.name === "devfeed_user_x_click",
      );
      assert.equal(Boolean(cookie), flag === "true");
      if (flag === "true") {
        assert.equal(cookie.value, clickId);
        assert.equal(cookie.httpOnly, true);
        assert.equal(cookie.sameSite, "Lax");
        assert.equal(
          (await page.evaluate(() => document.cookie)).includes("devfeed_user_x_click"),
          false,
        );
      }
      await page.evaluate(() =>
        fetch("/api/v1/user/auth/login?provider=github", { redirect: "manual" }),
      );
      assert.equal(loginCookies.includes(`devfeed_user_x_click=${clickId}`), flag === "true");
      console.log(`X click attribution browser check passed: flag=${flag ?? "unset"}`);
    } catch (error) {
      console.error(logs);
      throw error;
    } finally {
      await context.close();
      app.kill("SIGTERM");
      await new Promise((resolve) => app.once("exit", resolve));
      await new Promise((resolve) => userApi.close(resolve));
    }
  }
} finally {
  await browser.close();
}

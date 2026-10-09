import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";

const root = path.resolve(import.meta.dirname, "../../../..");
const browser = await chromium.launch({
  headless: true,
  args: ["--test-third-party-cookie-phaseout"],
});
try {
  for (const flag of process.env.DEVFEED_X_AUDIT_STAGE ? ["true"] : [undefined, "false", "true"]) {
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
      if (process.env.DEVFEED_X_AUDIT_STAGE) {
        const { default: lighthouse } = await import("lighthouse");
        const { launch } = await import("chrome-launcher");
        const samples = [];
        for (const formFactor of ["mobile", "desktop"]) {
          for (let run = 0; run < 3; run++) {
            const chrome = await launch({
              chromePath: chromium.executablePath(),
              chromeFlags: ["--headless", "--no-sandbox"],
            });
            try {
              const result = await lighthouse(
                `${origin}/legal/privacy`,
                {
                  port: chrome.port,
                  logLevel: "error",
                },
                {
                  extends: "lighthouse:default",
                  settings: {
                    onlyAudits: ["third-party-cookies", "inspector-issues"],
                    formFactor,
                    screenEmulation:
                      formFactor === "desktop"
                        ? {
                            mobile: false,
                            width: 1350,
                            height: 940,
                            deviceScaleFactor: 1,
                            disabled: false,
                          }
                        : {
                            mobile: true,
                            width: 412,
                            height: 823,
                            deviceScaleFactor: 1.75,
                            disabled: false,
                          },
                  },
                },
              );
              const audits = Object.fromEntries(
                ["third-party-cookies", "inspector-issues"].map((id) => [
                  id,
                  {
                    score: result.lhr.audits[id].score,
                    displayValue: result.lhr.audits[id].displayValue,
                    errorMessage: result.lhr.audits[id].errorMessage,
                    // Cookie names/domains only; never persist cookie values or click identifiers.
                    cookies:
                      id === "third-party-cookies"
                        ? (result.lhr.audits[id].details?.items ?? []).map((item) => ({
                            name: String(item.name).split("=")[0],
                            domain: item.url ? new URL(item.url).hostname : undefined,
                          }))
                        : undefined,
                  },
                ]),
              );
              samples.push({
                formFactor,
                run: run + 1,
                lighthouse: result.lhr.lighthouseVersion,
                browser: result.lhr.environment.hostUserAgent,
                audits,
              });
              if (process.env.DEVFEED_X_AUDIT_STAGE === "after") {
                for (const audit of Object.values(audits)) assert.equal(audit.score, 1);
              }
            } finally {
              await chrome.kill();
            }
          }
        }
        const directory = path.join(root, "reports/x-conversions");
        await mkdir(directory, { recursive: true });
        await writeFile(
          path.join(directory, `${process.env.DEVFEED_X_AUDIT_STAGE}.json`),
          JSON.stringify(samples, null, 2),
        );
        console.log(JSON.stringify(samples, null, 2));
        continue;
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
      await context.route(
        /https:\/\/(?:[^/]+\.)?(?:ads-twitter\.com|twitter\.com|t\.co)\//,
        (route) => {
          loaders += 1;
          return route.abort("blockedbyclient");
        },
      );
      await context.addInitScript(() => {
        window.__cspViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => {
          window.__cspViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
        });
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
      const html = await response.text();
      assert.equal(html.includes("twq('config','pc5f8')"), false);
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .waitFor();
      await page
        .locator(".sidebar")
        .getByRole("link", { name: "Connect your agent", exact: true })
        .click();
      await page.getByRole("heading", { name: "Connect your agent", exact: true }).waitFor();
      assert.equal(await page.locator("#x-pixel").count(), 0);
      assert.equal(await page.evaluate(() => typeof window.twq), "undefined");
      assert.equal(loaders, 0, "No X request occurs on initial render or client navigation");
      assert.deepEqual(await page.evaluate(() => window.__cspViolations), []);
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
      console.log(`X server-only conversion browser check passed: flag=${flag ?? "unset"}`);
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

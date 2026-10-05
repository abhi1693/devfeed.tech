import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";
import { pageContentSelector } from "./dast-selectors.mjs";

// Exchange settings and sessions through private pipes, without configurable file paths.
const settings = JSON.parse(readFileSync(0, "utf8"));
const allowed = new Set(settings.origins);
assert.ok([...allowed].every((origin) => new URL(origin).hostname === "127.0.0.1"));
let checkpoint = "launch";
let browser;
let pageNumber = 0;
const sessions = {};

async function visit(page, origin, path) {
  checkpoint = "navigate";
  let response = await page.goto(origin + path, { waitUntil: "domcontentloaded" });
  if (!response) response = await page.reload({ waitUntil: "domcontentloaded" });
  checkpoint = "response";
  assert.ok(response, `No response for page: ${path}`);
  assert.equal(response.status(), 200, `Failed page: ${path}`);
  checkpoint = "content";
  const content = pageContentSelector(path);
  await page.locator(content).waitFor();
  // Background polling/streams need not stop; capture each visible page for a bounded interval.
  await page.waitForTimeout(500);
}

try {
  browser = await chromium.launch({
    headless: true,
    proxy: { server: settings.proxy },
    args: ["--proxy-bypass-list=<-loopback>"],
  });
  for (const identity of ["anonymous", "reader", "admin"]) {
    const context = await browser.newContext();
    context.setDefaultTimeout(30_000);
    await context.route("**/*", (route) => {
      const url = new URL(route.request().url());
      return allowed.has(url.origin) ? route.continue() : route.abort();
    });
    const admin = identity === "admin";
    const origin = admin ? settings.admin : settings.web;
    const namespace = admin ? "admin" : "user";
    if (identity !== "anonymous") {
      const page = await context.newPage();
      checkpoint = "login";
      await page.goto(`${origin}/api/v1/${namespace}/auth/login`);
      await page.waitForURL(`${origin}/`);
      checkpoint = "identity";
      const response = await context.request.get(`${origin}/api/v1/${namespace}/auth/me`);
      assert.equal(response.status(), 200);
      const user = await response.json();
      assert.equal(user.subject, `browser-ci-${admin ? "admin" : "reader"}`);
      if (admin) assert.ok(user.roles.includes("superuser"));
      checkpoint = "cookies";
      const cookies = await context.cookies();
      const cookie = cookies.find((item) => item.name === `devfeed_${namespace}_session`);
      assert.ok(cookie?.httpOnly, "Real login must issue an HttpOnly session cookie");
      sessions[identity] = { cookie: `${cookie.name}=${cookie.value}`, csrf: user.csrf_token };
      await page.close();
    }
    const pages = admin
      ? ["/start", "/content/articles", "/users", "/content/sources", "/taxonomy/topics"]
      : identity === "reader"
        ? ["/", "/read-later", "/settings/profile", "/settings/topics"]
        : ["/", "/latest", "/sources", "/topics", "/search?q=CI", `/articles/${settings.article}`];
    for (const path of pages) {
      const page = await context.newPage();
      page.on("pageerror", () => console.error("DAST_BROWSER_JS"));
      pageNumber += 1;
      await visit(page, origin, path);
      console.error(`Scanned ${identity} page ${path.split("?", 1)[0]}`);
      await page.close();
    }
    checkpoint = "close";
    await context.close();
  }
  process.stdout.write(JSON.stringify(sessions));
} catch (error) {
  // Emit a fixed checkpoint only: Playwright errors may contain credentials and callback URLs.
  console.error(`DAST_BROWSER_FAILURE:${checkpoint}`);
  console.error(`DAST_BROWSER_PAGE:${pageNumber}`);
  if (typeof error?.message === "string" && error.message.includes("strict mode violation"))
    console.error("DAST_BROWSER_STRICT");
  process.exitCode = 1;
} finally {
  await browser?.close();
}

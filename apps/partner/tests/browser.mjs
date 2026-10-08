import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const account = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "API Checker",
  tier: "gold",
  benefits: ["Product placements", "Monthly performance reporting"],
  status: "active",
};
const beta = {
  ...account,
  id: "22222222-2222-2222-2222-222222222222",
  name: "BuildKit Studio",
  tier: "bronze",
  benefits: [],
};
const assets = [
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "API Checker launch campaign",
    kind: "ad",
    product_id: null,
    status: "active",
    impressions: 2400,
    clicks: 120,
    ctr: 5,
    measured_days: 1,
  },
];
const mock = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  const path = url.pathname;
  const mode = /devfeed_partner_session=(\w+)/.exec(request.headers.cookie ?? "")?.[1] ?? "";
  response.setHeader("Content-Type", "application/json");
  if (path.endsWith("/auth/config")) {
    response.end(JSON.stringify({ enabled: true, providers: [] }));
    return;
  }
  if (path.endsWith("/auth/login")) {
    response.writeHead(302, {
      "Set-Cookie": "devfeed_partner_session=partner; HttpOnly; Path=/; SameSite=Lax",
      Location: `http://127.0.0.1:${webPort}/`,
    });
    response.end();
    return;
  }
  if (path.endsWith("/auth/logout")) {
    response.writeHead(204, { "Set-Cookie": "devfeed_partner_session=; Max-Age=0; Path=/" });
    response.end();
    return;
  }
  if (!mode || mode === "denied") {
    response.writeHead(403);
    response.end("{}");
    return;
  }
  if (path.endsWith("/auth/me")) {
    response.end(
      JSON.stringify({
        subject: "alice",
        name: "Alex Partner",
        roles: [mode === "superuser" ? "superuser" : "partner"],
        csrf_token: "test-csrf",
      }),
    );
    return;
  }
  if (path === "/v1/partner/accounts") {
    if (request.method === "POST") {
      response.writeHead(201);
      response.end(JSON.stringify(account));
      return;
    }
    response.end(
      JSON.stringify({
        items: mode === "empty" ? [] : mode === "superuser" ? [account, beta] : [account],
        total: mode === "empty" ? 0 : mode === "superuser" ? 2 : 1,
      }),
    );
    return;
  }
  if (path.endsWith("/dashboard")) {
    const isBeta = path.includes(beta.id);
    if (isBeta && mode !== "superuser") {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    const days = url.searchParams.get("days");
    response.end(
      JSON.stringify({
        account: isBeta ? beta : account,
        start: days === "7" ? "2026-10-01" : "2026-09-08",
        end: "2026-10-07",
        totals: isBeta
          ? { impressions: 0, clicks: 0, ctr: null, measured_days: 0 }
          : {
              impressions: days === "7" ? 700 : 2400,
              clicks: days === "7" ? 35 : 120,
              ctr: 5,
              measured_days: 1,
            },
        last_updated_at: "2026-10-07T10:00:00Z",
        trend: isBeta
          ? []
          : [
              {
                day: "2026-10-07",
                impressions: days === "7" ? 700 : 2400,
                clicks: days === "7" ? 35 : 120,
              },
            ],
        assets: isBeta
          ? []
          : days === "7"
            ? [{ ...assets[0], impressions: 700, clicks: 35 }]
            : assets,
        asset_total: isBeta ? 0 : 1,
      }),
    );
    return;
  }
  response.writeHead(404);
  response.end("{}");
});
await new Promise((resolve) => mock.listen(0, "127.0.0.1", resolve));
const apiPort = mock.address().port;
const reserve = createServer();
await new Promise((resolve) => reserve.listen(0, "127.0.0.1", resolve));
const webPort = reserve.address().port;
await new Promise((resolve) => reserve.close(resolve));
const origin = `http://127.0.0.1:${webPort}`;
const next = spawn(
  process.execPath,
  [
    "node_modules/next/dist/bin/next",
    "start",
    "apps/partner",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(webPort),
  ],
  {
    cwd: root,
    env: {
      ...process.env,
      DEVFEED_PARTNER_API_URL: `http://127.0.0.1:${apiPort}`,
      DEVFEED_PARTNER_BASE_URL: origin,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let logs = "";
next.stdout.on("data", (chunk) => {
  logs += chunk;
});
next.stderr.on("data", (chunk) => {
  logs += chunk;
});
let browser;
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      if ((await fetch(origin + "/login")).ok) break;
    } catch {}
    if (attempt === 99) throw new Error("Portal failed to start: " + logs);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(origin);
  await page.waitForURL("**/login");
  await page.getByRole("heading", { name: "Sign in to DevFeed Partners" }).waitFor();
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({ path: root + "reports/partner/login-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/login-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("link", { name: "Continue to sign in" }).click();
  await page.getByRole("heading", { name: "Gold", exact: true }).waitFor();
  await page.getByRole("button", { name: "User menu: Alex Partner" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "Sign out" }).waitFor();
  await page.getByRole("menu").evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
  });
  await page.screenshot({ path: root + "reports/partner/avatar-menu-desktop.png", fullPage: true });
  await page.keyboard.press("Escape");
  assert.equal(
    await page
      .getByRole("button", { name: "User menu: Alex Partner" })
      .getAttribute("aria-expanded"),
    "false",
  );
  assert.equal(await page.getByText("Manage partnerships").count(), 0);
  assert.equal(await page.getByRole("heading", { name: "Performance", exact: true }).count(), 0);
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  assert.equal(await page.getByRole("combobox", { name: "Reporting period" }).count(), 0);
  await page.screenshot({ path: root + "reports/partner/overview-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page.screenshot({ path: root + "reports/partner/overview-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "User menu: Alex Partner" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.getByRole("menu").evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
  });
  await page.screenshot({ path: root + "reports/partner/avatar-menu-mobile.png", fullPage: true });
  await page.getByRole("menuitem", { name: "Overview", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/${account.id}`);
  await page.getByRole("heading", { name: "Gold", exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "User menu: Alex Partner" })
      .getAttribute("aria-expanded"),
    "false",
  );
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("link", { name: "Performance", exact: true }).click();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Daily activity" }).waitFor();
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  assert.equal(await page.getByText("2,400", { exact: true }).count(), 2);
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 30 days" }).click();
  await page.getByText("2,400", { exact: true }).first().waitFor();
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({ path: root + "reports/partner/desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page.getByRole("link", { name: "Products & ads", exact: true }).click();
  assert.equal(
    await page.getByRole("button", { name: "Navigation" }).getAttribute("aria-expanded"),
    "false",
  );
  await page.screenshot({ path: root + "reports/partner/mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

  await page.waitForURL("**/assets?*");
  assert.equal(new URL(page.url()).hash, "");
  assert.equal(await page.getByRole("heading", { name: "Daily activity" }).count(), 0);
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page.reload();
  await page.getByText("API Checker launch campaign", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Navigation" }).click();
  await page.getByRole("link", { name: "Performance", exact: true }).click();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Daily activity" }).waitFor();
  assert.equal(new URL(page.url()).pathname, `/${account.id}/performance`);
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.reload();
  assert.match(
    await page.getByRole("combobox", { name: "Reporting period" }).innerText(),
    /Last 7 days/,
  );
  await page.getByRole("heading", { name: "Daily activity" }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/${account.id}`);
  await page.getByRole("heading", { name: "Gold", exact: true }).waitFor();
  await page.goBack();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Daily activity" }).waitFor();
  await context.addCookies([{ name: "devfeed_partner_session", value: "superuser", url: origin }]);
  await page.goto(origin);
  assert.equal(await page.getByRole("link", { name: "Manage partnerships" }).count(), 0);
  assert.equal(await page.getByRole("heading", { name: "Manage partnerships" }).count(), 0);
  await page.getByRole("combobox", { name: "Partner account" }).click();
  await page.getByRole("option", { name: "BuildKit Studio", exact: true }).click();
  await page.getByRole("heading", { name: "Bronze", exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, `/${beta.id}`);
  assert.equal(new URL(page.url()).searchParams.has("account"), false);
  await page.goto(origin + `/performance?account=${beta.id}&days=7`);
  await page.waitForURL((url) => url.pathname === `/${beta.id}/performance`);
  assert.equal(new URL(page.url()).searchParams.get("days"), "7");
  assert.equal(new URL(page.url()).searchParams.has("account"), false);
  const foreign = await page.goto(origin + "/foreign/performance");
  assert.equal(foreign.status(), 404);
  const removed = await page.goto(origin + "/management");
  assert.equal(removed.status(), 404);
  await context.addCookies([{ name: "devfeed_partner_session", value: "empty", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "No partner accounts yet" }).waitFor();
  await page.getByRole("button", { name: "User menu: Alex Partner" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
  await context.addCookies([{ name: "devfeed_partner_session", value: "denied", url: origin }]);
  await page.goto(origin);
  await page.waitForURL("**/login?error=access_denied");
  await page.getByText("Your account needs the partner role to access this portal.").waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "Partner reporting routes, refresh, history, mobile navigation, empty state, and removed management access passed.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => mock.close(resolve));
}

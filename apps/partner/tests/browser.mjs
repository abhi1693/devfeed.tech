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
  tier: "Growth",
  benefits: ["Product placements", "Monthly performance reporting"],
  status: "active",
};
const beta = {
  ...account,
  id: "22222222-2222-2222-2222-222222222222",
  name: "BuildKit Studio",
  tier: "Launch",
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
let members = [{ subject: "alice", issuer: "https://identity.example" }];
const mock = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  const path = url.pathname;
  const mode = /devfeed_partner_session=(\w+)/.exec(request.headers.cookie ?? "")?.[1] ?? "";
  response.setHeader("Content-Type", "application/json");
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
  if (path.endsWith("/members")) {
    if (request.method === "PUT") {
      let body = "";
      for await (const chunk of request) body += chunk;
      members.push({ subject: JSON.parse(body).subject, issuer: "https://identity.example" });
      response.writeHead(204);
      response.end();
      return;
    }
    response.end(JSON.stringify(members));
    return;
  }
  if (path.includes("/members/") && request.method === "DELETE") {
    members = members.filter((member) => member.subject !== path.split("/").at(-1));
    response.writeHead(204);
    response.end();
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
  await page.getByRole("link", { name: "Sign in with Zitadel" }).click();
  await page.getByRole("heading", { name: "Growth", exact: true }).waitFor();
  assert.equal(await page.getByText("Manage partnerships").count(), 0);
  assert.equal(await page.getByText("2,400", { exact: true }).count(), 3);
  await page.getByLabel("Reporting period").selectOption("7");
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.getByLabel("Reporting period").selectOption("30");
  await page.getByText("2,400", { exact: true }).first().waitFor();
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({ path: root + "reports/partner/desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: root + "reports/partner/mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await context.addCookies([{ name: "devfeed_partner_session", value: "superuser", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "Manage partnerships" }).waitFor();
  await page.getByText("Account members", { exact: true }).click();
  await page.getByLabel("Zitadel subject ID").fill("bob");
  await page.getByRole("button", { name: "Add member", exact: true }).click();
  await page.getByRole("button", { name: "Remove member bob" }).waitFor();
  await page.getByRole("button", { name: "Remove member bob" }).click();
  await page.getByRole("button", { name: "Remove member bob" }).waitFor({ state: "detached" });
  await page.getByLabel("Partner account").selectOption(beta.id);
  await page.getByText(/No delivery measurements/).waitFor();
  await page.screenshot({ path: root + "reports/partner/superuser.png", fullPage: true });
  await context.addCookies([{ name: "devfeed_partner_session", value: "empty", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "No partner accounts yet" }).waitFor();
  await context.addCookies([{ name: "devfeed_partner_session", value: "denied", url: origin }]);
  await page.goto(origin);
  await page.waitForURL("**/login?error=access_denied");
  await page
    .getByText("Your account needs the partner or superuser role to access this portal.")
    .waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "Partner portal desktop/mobile, account scope, superuser membership management, empty state, and denied access passed.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => mock.close(resolve));
}

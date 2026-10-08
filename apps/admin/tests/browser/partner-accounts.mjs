import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const account = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "API Checker",
  tier: "gold",
  status: "active",
  benefits: ["Product placements"],
};
let members = [{ subject: "alice", issuer: "https://identity.example" }];
const writes = [];
const memberUser = {
  id: "33333333-3333-3333-3333-333333333333",
  name: "Bob Partner",
  email: "bob@example.test",
};
const catalogProduct = {
  id: "44444444-4444-4444-4444-444444444444",
  name: "API Checker",
  slug: "api-checker",
  status: "active",
};
let assets = [];
const fixture = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
  res.setHeader("Content-Type", "application/json");
  if (path.endsWith("/auth/me")) {
    if (!(req.headers.cookie ?? "").includes("devfeed_admin_session=admin")) {
      res.writeHead(401);
      res.end("{}");
      return;
    }
    body = {
      subject: "fixture",
      name: "Admin reviewer",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture-csrf",
    };
  } else if (path.endsWith("/auth/config")) body = { enabled: true, providers: [] };
  else if (path.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0, overview_days: 30 } };
  else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path.endsWith("/ai/connection"))
    body = { state: "disabled", message: "Disabled", quota: [] };
  else if (path === "/v1/admin/partner-tools")
    body = { items: [catalogProduct], total: 1, limit: 25, offset: 0 };
  else if (path === "/v1/admin/users")
    body = { items: [memberUser], total: 1, limit: 25, offset: 0 };
  else if (path === `/v1/admin/users/${memberUser.id}`) body = memberUser;
  else if (path === "/v1/admin/partner-accounts/tiers") {
    const benefits = [
      "Partner portal access",
      "Product and ad performance reporting",
      "Product placement opportunities",
      "Sponsored ad campaign opportunities",
      "Priority campaign support",
      "Custom partnership and campaign planning",
    ];
    body = ["bronze", "silver", "gold", "platinum", "diamond"].map((tier, index) => ({
      tier,
      benefits: benefits.slice(0, index + 2),
    }));
  } else if (path === "/v1/admin/partner-accounts") {
    if (req.method === "POST") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      body = { ...account, ...payload };
      res.statusCode = 201;
    } else body = { items: [account], total: 1 };
  } else if (path === `/v1/admin/partner-accounts/${account.id}` && req.method === "PUT") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
    Object.assign(account, payload);
    body = account;
  } else if (path.endsWith("/assets") && req.method === "POST") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
    body = { id: "22222222-2222-2222-2222-222222222222", ...payload };
    assets.push(body);
  } else if (path.includes("/assets/") && req.method === "PUT") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
    Object.assign(assets[0], payload);
    body = assets[0];
  } else if (path.endsWith("/dashboard")) body = { account, assets, asset_total: assets.length };
  else if (path.endsWith("/members")) {
    if (req.method === "PUT") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      assert.equal(payload.user_id, memberUser.id);
      members.push({
        subject: "bob",
        issuer: "https://identity.example",
        user_id: memberUser.id,
        name: memberUser.name,
        email: memberUser.email,
      });
      res.writeHead(204);
      res.end();
      return;
    } else body = members;
  } else if (path.includes("/members/") && req.method === "DELETE") {
    members = members.filter((member) => member.subject !== path.split("/").at(-1));
    writes.push({ path, csrf: req.headers["x-csrf-token"] });
    res.writeHead(204);
    res.end();
    return;
  } else {
    res.statusCode = 404;
  }
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const apiPort = fixture.address().port;
const reserve = createServer();
await new Promise((resolve) => reserve.listen(0, "127.0.0.1", resolve));
const port = reserve.address().port;
await new Promise((resolve) => reserve.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const next = spawn(
  process.execPath,
  [
    "node_modules/next/dist/bin/next",
    "start",
    "apps/admin",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: root,
    env: {
      ...process.env,
      DEVFEED_ADMIN_API_URL: `http://127.0.0.1:${apiPort}`,
      DEVFEED_ADMIN_BASE_URL: origin,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);
let logs = "";
next.stdout.on("data", (c) => (logs += c));
next.stderr.on("data", (c) => (logs += c));
let browser;
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(origin + "/login")).ok) break;
    } catch {}
    if (i === 99) throw new Error(logs);
    await new Promise((r) => setTimeout(r, 200));
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await context.addCookies([{ name: "devfeed_partner_session", value: "partner", url: origin }]);
  await page.goto(origin + "/partnerships/accounts");
  await page.waitForURL("**/login");
  await context.addCookies([{ name: "devfeed_admin_session", value: "admin", url: origin }]);
  await page.goto(origin + "/partnerships/accounts");
  await page.getByRole("heading", { name: "Partner accounts", exact: true }).waitFor();
  await page.getByRole("link", { name: "API Checker", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  await page.getByRole("link", { name: "Add member", exact: true }).click();
  await page.getByRole("combobox", { name: "User", exact: true }).click();
  await page.getByPlaceholder("Search users…").fill("Bob");
  await page.getByRole("option", { name: "Bob Partner", exact: true }).click();
  await page.getByRole("button", { name: "Add member", exact: true }).click();
  await page.getByRole("button", { name: "Remove member bob" }).waitFor();
  await page.getByRole("link", { name: "Bob Partner", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("link", { name: "Bob Partner", exact: true }).getAttribute("href"),
    `/users/${memberUser.id}`,
  );
  await page.getByText(memberUser.email, { exact: true }).waitFor();
  await page.getByText("User details unavailable", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Remove member bob" }).click();
  await page.getByRole("button", { name: "Remove member bob" }).waitFor({ state: "detached" });
  await page.goto(origin + "/partnerships/accounts");
  await page.getByRole("link", { name: "Create partner account", exact: true }).click();
  await page
    .getByLabel(/Partner name/)
    .first()
    .fill("New partner");
  await page
    .getByRole("region", { name: "Selected tier benefits" })
    .getByText("Partner portal access", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Bronze",
    "Silver",
    "Gold",
    "Platinum",
    "Diamond",
  ]);
  assert.equal(await page.getByLabel("Benefits, one per line").count(), 0);
  await page.getByRole("option", { name: "Silver", exact: true }).click();
  await page
    .getByRole("region", { name: "Selected tier benefits" })
    .getByText("Product placement opportunities", { exact: true })
    .waitFor();
  assert.equal(
    await page.getByText("Custom partnership and campaign planning", { exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  assert.equal(writes.at(-1).payload.tier, "silver");
  assert.equal("benefits" in writes.at(-1).payload, false);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/edit`);
  await page.reload();
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Diamond", exact: true }).click();
  await page
    .getByRole("region", { name: "Selected tier benefits" })
    .getByText("Custom partnership and campaign planning", { exact: true })
    .waitFor();
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({
    path: root + "reports/partner/tier-benefits-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/tier-benefits-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("combobox", { name: "Status" }).click();
  await page.getByRole("option", { name: "Paused", exact: true }).click();
  await page.getByRole("button", { name: "Save partnership" }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await page.getByText("Diamond", { exact: true }).waitFor();
  assert.equal(writes.at(-1).payload.status, "paused");
  assert.equal(writes.at(-1).payload.tier, "diamond");
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  await page.getByRole("link", { name: "Associate asset", exact: true }).click();
  await page.getByLabel(/Asset name/).fill("Launch ad");
  await page.getByRole("combobox", { name: "Catalog product" }).click();
  await page.getByPlaceholder("Search products…").fill("API");
  await page.getByRole("option", { name: /API Checker/ }).click();
  await page.getByRole("button", { name: "Associate asset", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/related`);
  assert.equal(writes.at(-1).payload.product_id, catalogProduct.id);
  assert.equal(writes.at(-1).payload.kind, "product");
  await page.getByRole("link", { name: "Edit Launch ad" }).click();
  await page.waitForURL("**/assets/*/edit*");
  await page.reload();
  await page
    .getByRole("combobox", { name: "Catalog product" })
    .getByText("API Checker", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Type" }).click();
  await page.getByRole("option", { name: "Ad", exact: true }).click();
  await page.getByRole("combobox", { name: "Catalog product" }).click();
  await page.getByRole("option", { name: "None", exact: true }).click();
  await page.getByRole("combobox", { name: "Status" }).click();
  await page.getByRole("option", { name: "Active", exact: true }).click();
  await page.getByRole("button", { name: "Save asset" }).click();
  await page.waitForURL(`**/accounts/${account.id}/related`);
  await page.getByText("Launch ad", { exact: true }).waitFor();
  assert.equal(writes.at(-1).payload.status, "active");
  assert.equal(writes.at(-1).payload.product_id, null);
  assert.equal(writes.length, 6);
  assert.ok(
    writes.every(
      (write) =>
        write.path.startsWith("/v1/admin/partner-accounts") && write.csrf === "fixture-csrf",
    ),
  );
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({
    path: root + "reports/partner/admin-management-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/admin-management-mobile.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  console.log(
    "Admin account list/detail/create/edit, membership and asset workflows passed on desktop/mobile; partner cookie denied.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

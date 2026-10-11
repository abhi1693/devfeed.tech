import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import {
  emptyAudience,
  audienceTopicOptions,
  selectedAudience,
  selectAudience,
  checkAudienceSelection,
} from "../../../../scripts/testing/audience-targeting.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const account = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "API Checker",
  tier: "gold",
  status: "active",
  benefits: ["Product placements"],
  organic_enabled: true,
  auto_include_products: true,
  promotion_surfaces: ["article", "topic", "feed"],
  allowed_promotion_kinds: ["product", "ad"],
  effective_promotion_kind: "ad",
  promotion_kind_source: "tier",
  promotion_kind: null,
  approval_mode: "qualified",
  outbound_destination: "product_url",
  advanced_targeting: false,
  audience: structuredClone(emptyAudience),
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
  status: "approved",
};
const destinationProduct = {
  id: catalogProduct.id,
  name: "API Checker Cloud",
  product_url: "https://product.example/start?plan=team&utm_source=devfeed#quickstart",
  listing_url:
    "https://shipyard.example/products/api-checker?ref=catalog&utm_source=devfeed#overview",
};
const previewRequests = [];
let assets = [];
const campaigns = [];
let productExcluded = false;
let appearance = { theme: "light" };
const inventoryRequests = [];
const audienceRequests = [];
const inventoryProducts = Array.from({ length: 60 }, (_, index) => ({
  ...catalogProduct,
  id:
    index === 0 ? catalogProduct.id : `66666666-6666-4666-8666-${String(index).padStart(12, "0")}`,
  name: index === 0 ? catalogProduct.name : `Inventory tool ${index + 1}`,
  status: index === 0 ? "pending" : "approved",
  commercial_approved: true,
  approval_source: index === 0 ? "trusted" : "qualified",
  technologies: [
    "Kubernetes",
    "PostgreSQL",
    "OpenTelemetry",
    "Amazon Web Services",
    "GitHub Actions",
    "Infrastructure-as-code-with-an-extremely-long-technology-name",
    "TypeScript",
    "Cloudflare Workers",
  ],
  globally_excluded: false,
}));
function updatePolicy(payload) {
  Object.assign(account, payload);
  const paid = ["gold", "platinum", "diamond"].includes(account.tier);
  account.allowed_promotion_kinds = paid ? ["product", "ad"] : ["product"];
  account.effective_promotion_kind = account.promotion_kind ?? (paid ? "ad" : "product");
  account.promotion_kind_source = account.promotion_kind ? "account" : "tier";
  account.advanced_targeting = ["platinum", "diamond"].includes(account.tier);
}
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
    body = { appearance, defaults: { refresh_seconds: 0, overview_days: 30 } };
  else if (path.endsWith("/settings/appearance") && req.method === "PUT") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    appearance = JSON.parse(raw);
    assert.equal(req.headers["x-csrf-token"], "fixture-csrf");
    body = { appearance, defaults: { refresh_seconds: 0, overview_days: 30 } };
  } else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path.endsWith("/ai/connection"))
    body = { state: "disabled", message: "Disabled", quota: [] };
  else if (path === "/v1/admin/partner-tools") {
    const params = new URL(req.url, "http://localhost").searchParams;
    const products = [
      catalogProduct,
      {
        ...catalogProduct,
        id: "55555555-5555-4555-8555-555555555555",
        name: "Rejected product",
        status: "rejected",
      },
    ];
    const items = products.filter(
      (product) => !params.has("status") || product.status === params.get("status"),
    );
    body = { items, total: items.length, limit: 25, offset: 0 };
  } else if (path === "/v1/admin/users")
    body = { items: [memberUser], total: 1, limit: 25, offset: 0 };
  else if (path === `/v1/admin/users/${memberUser.id}`) body = memberUser;
  else if (path === "/v1/admin/partner-accounts/tiers") {
    const benefits = [
      "Partner portal access",
      "Product and ad performance reporting",
      "Relevant product recommendations",
      "Product promotion planning",
      "Sponsored ad campaign opportunities",
      "Priority campaign support",
      "Custom partnership and campaign planning",
    ];
    body = ["bronze", "silver", "gold", "platinum", "diamond"].map((tier, index) => ({
      tier,
      benefits: benefits.slice(0, index + 3),
      allowed_promotion_kinds: index >= 2 ? ["product", "ad"] : ["product"],
      default_promotion_kind: index >= 2 ? "ad" : "product",
      advanced_targeting: index >= 3,
    }));
  } else if (path.endsWith("/audience-topics")) {
    const params = new URL(req.url, "http://localhost").searchParams;
    audienceRequests.push(Object.fromEntries(params));
    body = audienceTopicOptions(params);
  } else if (path === "/v1/admin/partner-accounts") {
    if (req.method === "POST") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      updatePolicy(payload);
      body = account;
      res.statusCode = 201;
    } else body = { items: [account], total: 1 };
  } else if (path === `/v1/admin/partner-accounts/${account.id}` && req.method === "PUT") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
    updatePolicy(payload);
    body = account;
  } else if (path === `/v1/admin/partner-accounts/${account.id}/destination-preview`) {
    previewRequests.push(path);
    body = { product: destinationProduct };
  } else if (path === `/v1/admin/partner-accounts/${account.id}/inventory`) {
    const params = new URL(req.url, "http://localhost").searchParams;
    inventoryRequests.push(Object.fromEntries(params));
    const matching = inventoryProducts.filter(
      (product) =>
        (!params.has("product_id") || params.get("product_id") === product.id) &&
        product.name.toLowerCase().includes((params.get("q") ?? "").toLowerCase()),
    );
    const offset = Number(params.get("offset") ?? 0);
    const limit = Number(params.get("limit") ?? 25);
    const items = matching.slice(offset, offset + limit).map((product) => ({
      ...product,
      excluded: product.id === catalogProduct.id && productExcluded,
    }));
    body = { items, total: matching.length };
  } else if (path.endsWith(`/products/${catalogProduct.id}/promotion`) && req.method === "PUT") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const payload = JSON.parse(raw);
    writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
    productExcluded = payload.excluded;
    body = { account_id: account.id, product_id: catalogProduct.id, ...payload };
  } else if (path.endsWith("/campaigns")) {
    if (req.method === "POST") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      body = {
        id: `22222222-2222-4222-8222-${String(campaigns.length + 1).padStart(12, "0")}`,
        account_id: account.id,
        ...payload,
        impressions: 0,
        clicks: 0,
        ctr: null,
      };
      campaigns.push(body);
      res.statusCode = 201;
    } else body = { items: campaigns, total: campaigns.length };
  } else if (path.includes("/campaigns/")) {
    const campaign = campaigns.find((item) => item.id === path.split("/").at(-1));
    if (!campaign) {
      res.statusCode = 404;
      body = { detail: "Campaign not found." };
    } else if (req.method === "PUT") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      Object.assign(campaign, payload);
      body = campaign;
    } else body = campaign;
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
  assert.equal(await page.getByLabel("Product exposure limit", { exact: false }).count(), 0);
  assert.equal(await page.locator('[name="frequency_cap"]').count(), 0);
  await page
    .getByRole("region", { name: "Click destination preview", exact: true })
    .getByText("A preview will appear when this account has an accepted product.", { exact: true })
    .waitFor();
  assert.equal(previewRequests.length, 0, "new accounts do not fetch another account's product");
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
  assert.equal(await page.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  await page
    .getByRole("region", { name: "Selected tier benefits" })
    .getByText("Product promotion planning", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Default promotion", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Inherit tier (Product recommendation)",
    "Product recommendation",
  ]);
  await page
    .getByRole("option", { name: "Inherit tier (Product recommendation)", exact: true })
    .click();
  await page.getByRole("combobox", { name: "Imported product acceptance", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Automatic capability checks",
    "Trust connected partner imports",
  ]);
  await page.getByRole("option", { name: "Automatic capability checks", exact: true }).click();
  assert.equal(
    await page.getByText("Custom partnership and campaign planning", { exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "Create account", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  assert.equal(writes.at(-1).payload.tier, "silver");
  assert.equal("benefits" in writes.at(-1).payload, false);
  assert.equal(writes.at(-1).payload.organic_enabled, true);
  assert.equal(writes.at(-1).payload.auto_include_products, true);
  assert.equal("frequency_cap" in writes.at(-1).payload, false);
  assert.equal(writes.at(-1).payload.promotion_kind, null);
  // Older cached account responses must not restore or normalize a removed setting.
  account.frequency_cap = 20;
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/edit`);
  await page.reload();
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Diamond", exact: true }).click();
  const audience = page.getByRole("region", { name: "Audience targeting", exact: true });
  await selectAudience(page, audience, audienceRequests);
  await page.getByText(/Effective: Sponsored · inherited from tier/).waitFor();
  await page.getByRole("combobox", { name: "Default promotion", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Inherit tier (Sponsored)",
    "Product recommendation",
    "Sponsored",
  ]);
  await page.getByRole("option", { name: "Inherit tier (Sponsored)", exact: true }).click();
  await page.getByRole("combobox", { name: "Imported product acceptance", exact: true }).click();
  await page.getByRole("option", { name: "Trust connected partner imports", exact: true }).click();
  const destinationPreview = page.getByRole("region", {
    name: "Click destination preview",
    exact: true,
  });
  const previewLink = destinationPreview.getByRole("link");
  await previewLink.waitFor();
  assert.equal(
    await destinationPreview
      .getByRole("link", { name: destinationProduct.product_url, exact: true })
      .count(),
    1,
  );
  assert.equal(await previewLink.getAttribute("href"), destinationProduct.product_url);
  assert.equal(await previewLink.textContent(), destinationProduct.product_url);
  assert.equal(
    await destinationPreview.getByText(destinationProduct.name, { exact: true }).count(),
    0,
  );
  assert.equal(await destinationPreview.locator("img").count(), 0);
  const writesBeforeDestinationChange = writes.length;
  await page.getByRole("combobox", { name: "Product click destination", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Product website",
    "Partner listing",
  ]);
  assert.equal(await page.getByRole("option", { name: "None", exact: true }).count(), 0);
  await page.getByRole("option", { name: "Partner listing", exact: true }).click();
  assert.equal(await previewLink.getAttribute("href"), destinationProduct.listing_url);
  assert.equal(await previewLink.textContent(), destinationProduct.listing_url);
  await destinationPreview.getByText(destinationProduct.listing_url, { exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Product click destination", exact: true }).click();
  await page.getByRole("option", { name: "Product website", exact: true }).click();
  assert.equal(await previewLink.getAttribute("href"), destinationProduct.product_url);
  await page.getByRole("combobox", { name: "Product click destination", exact: true }).click();
  await page.getByRole("option", { name: "Partner listing", exact: true }).click();
  assert.equal(
    writes.length,
    writesBeforeDestinationChange,
    "preview updates before saving account preferences",
  );
  async function captureDestinationPreview(width) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    await destinationPreview.scrollIntoViewIfNeeded();
    const control = await page
      .getByRole("combobox", { name: "Product click destination", exact: true })
      .boundingBox();
    const previewBounds = await destinationPreview.boundingBox();
    if (width >= 1024)
      assert.ok(
        previewBounds.x >= control.x + control.width,
        "desktop preview sits to the right of destination selector",
      );
    else
      assert.ok(
        previewBounds.y >= control.y + control.height,
        "mobile preview follows the selector",
      );
    assert.ok(previewBounds.x >= 0 && previewBounds.x + previewBounds.width <= width + 1);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    assert.ok(previewBounds.height < 160, "URL preview remains compact");
    await mkdir(root + "reports/partner", { recursive: true });
    await page.screenshot({
      path:
        root +
        `reports/partner/admin-destination-preview-${width === 390 ? "mobile" : "desktop"}.png`,
      fullPage: true,
    });
  }
  await captureDestinationPreview(1280);
  await captureDestinationPreview(390);
  await page.setViewportSize({ width: 1440, height: 1100 });
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
  await audience.screenshot({
    path: root + "reports/partner/audience-admin-mobile.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await audience.screenshot({
    path: root + "reports/partner/audience-admin-desktop.png",
    animations: "disabled",
  });
  await page.getByRole("combobox", { name: "Status" }).click();
  await page.getByRole("option", { name: "Paused", exact: true }).click();
  await page.getByLabel("Reader feeds", { exact: true }).uncheck();
  assert.equal(await page.getByLabel("Product exposure limit", { exact: false }).count(), 0);
  await page.getByRole("button", { name: "Save partnership" }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await page.getByText("Diamond", { exact: true }).waitFor();
  assert.equal(writes.at(-1).payload.status, "paused");
  assert.equal(writes.at(-1).payload.tier, "diamond");
  assert.deepEqual(writes.at(-1).payload.promotion_surfaces, ["article", "topic"]);
  assert.equal("frequency_cap" in writes.at(-1).payload, false);
  assert.equal(
    account.frequency_cap,
    20,
    "saving other settings does not normalize the legacy cap",
  );
  delete account.frequency_cap;
  assert.equal(writes.at(-1).payload.promotion_kind, null);
  assert.equal(writes.at(-1).payload.approval_mode, "trusted");
  assert.equal(writes.at(-1).payload.outbound_destination, "listing_url");
  assert.deepEqual(writes.at(-1).payload.audience, selectedAudience);
  await page.getByText("Promotion defaults", { exact: true }).waitFor();
  assert.equal(await page.getByText("Product exposure limit", { exact: true }).count(), 0);
  assert.equal(await page.getByText(/per reader installation per UTC day/).count(), 0);
  await page
    .getByRole("navigation", { name: "Object sections" })
    .getByRole("link", { name: "Products", exact: true })
    .click();
  await page.getByRole("link", { name: catalogProduct.name, exact: true }).waitFor();
  const inventory = page.getByRole("region", { name: "Account products", exact: true });
  assert.equal(await inventory.getByRole("button", { name: "Search", exact: true }).count(), 0);
  await inventory.getByRole("button", { name: "Next", exact: true }).click();
  await inventory.getByRole("link", { name: "Inventory tool 26", exact: true }).waitFor();
  assert.equal(inventoryRequests.at(-1).offset, "25");
  const productSearch = inventory.getByRole("textbox", { name: "Product name", exact: true });
  await productSearch.fill("API");
  await inventory.getByRole("link", { name: catalogProduct.name, exact: true }).waitFor();
  assert.equal(inventoryRequests.at(-1).q, "API");
  assert.equal(inventoryRequests.at(-1).offset, "0");
  assert.equal(await productSearch.evaluate((input) => input === document.activeElement), true);
  await productSearch.fill("Not a catalog match");
  await productSearch.press("Enter");
  await inventory.getByText(/No matching imported products/).waitFor();
  assert.equal(inventoryRequests.at(-1).q, "Not a catalog match");
  await inventory.getByRole("button", { name: "Clear product name", exact: true }).click();
  await inventory.getByRole("link", { name: catalogProduct.name, exact: true }).waitFor();
  assert.equal(inventoryRequests.at(-1).q, undefined);
  assert.equal(inventoryRequests.at(-1).offset, "0");
  await productSearch.fill("API");
  await inventory
    .getByRole("link", { name: "Inventory tool 2", exact: true })
    .waitFor({ state: "detached" });
  assert.equal(inventoryRequests.at(-1).q, "API");
  await inventory.getByText("Accepted", { exact: true }).waitFor();
  await inventory.getByText("Trusted partner", { exact: true }).waitFor();

  async function inventoryLayout(width, theme = "light") {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    const table = inventory.getByRole("table", { name: "Account products", exact: true });
    const bounds = await table.evaluate((element) => {
      const container = element.closest('[data-slot="table-container"]');
      return {
        pageWidth: document.documentElement.scrollWidth,
        viewport: innerWidth,
        containerWidth: container.clientWidth,
        scrollWidth: container.scrollWidth,
        headers: [...element.querySelectorAll("th")].map((cell) => ({
          name: cell.textContent.trim(),
          left: cell.getBoundingClientRect().left,
          right: cell.getBoundingClientRect().right,
        })),
      };
    });
    assert.ok(bounds.pageWidth <= bounds.viewport, "inventory never overflows the page");
    assert.ok(bounds.containerWidth <= bounds.viewport, "mobile table scrolling stays contained");
    if (width >= 1024) {
      assert.ok(
        bounds.scrollWidth <= bounds.containerWidth + 1,
        "desktop columns fit without scrolling",
      );
      assert.ok(bounds.headers.every((cell) => cell.left >= 0 && cell.right <= width + 1));
      const action = await inventory
        .getByRole("button", { name: `Exclude ${catalogProduct.name}`, exact: true })
        .boundingBox();
      assert.ok(action.x >= 0 && action.x + action.width <= width + 1);
    } else {
      await inventory
        .getByRole("button", { name: `Exclude ${catalogProduct.name}`, exact: true })
        .scrollIntoViewIfNeeded();
      const action = await inventory
        .getByRole("button", { name: `Exclude ${catalogProduct.name}`, exact: true })
        .boundingBox();
      assert.ok(
        action.x >= 0 && action.x + action.width <= width + 1,
        "mobile actions remain reachable",
      );
      await page.screenshot({
        path: root + `reports/partner/account-products-${theme}-mobile-actions.png`,
        fullPage: true,
      });
      await table.evaluate((element) => {
        element.closest('[data-slot="table-container"]').scrollLeft = 0;
      });
    }
    await page.screenshot({
      path:
        root +
        `reports/partner/account-products-${theme}-${width === 390 ? "mobile" : `desktop-${width}`}.png`,
      fullPage: true,
    });
  }
  await inventoryLayout(1440);
  await inventoryLayout(1280);
  await inventoryLayout(390);
  await page
    .getByRole("button", { name: "Theme: Light. Switch to dark theme", exact: true })
    .click();
  await page.waitForFunction(() => document.documentElement.classList.contains("dark"));
  await inventoryLayout(1280, "dark");
  await inventoryLayout(390, "dark");
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: `Exclude ${catalogProduct.name}`, exact: true }).click();
  await page.getByRole("button", { name: `Allow ${catalogProduct.name}`, exact: true }).waitFor();
  assert.equal(writes.at(-1).payload.excluded, true);
  await page.getByRole("button", { name: `Allow ${catalogProduct.name}`, exact: true }).click();
  await page.getByText("Inherits account defaults", { exact: true }).waitFor();
  assert.equal(writes.at(-1).payload.excluded, false);
  await page.getByRole("link", { name: "Campaigns", exact: true }).click();
  await page.getByRole("link", { name: "Create paid campaign", exact: true }).click();
  await page.getByLabel(/Campaign name/).fill("October catalog promotion");
  await page.getByLabel("Starts", { exact: false }).fill("2026-10-01T00:00");
  await page.getByLabel("Ends", { exact: false }).fill("2026-11-01T00:00");
  await page.getByLabel("Daily impression limit", { exact: false }).fill("250");
  await page.getByRole("combobox", { name: "Campaign status", exact: true }).click();
  await page.getByRole("option", { name: "Active", exact: true }).click();
  await page.getByRole("button", { name: "Create campaign", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/campaigns`);
  assert.equal(writes.at(-1).payload.targeting, "all");
  assert.equal(writes.at(-1).payload.status, "active");
  assert.equal(writes.at(-1).payload.daily_impression_limit, 250);
  assert.equal(
    writes.at(-1).payload.audience,
    null,
    "Dated campaigns inherit account audience by default",
  );
  assert.deepEqual(writes.at(-1).payload.surfaces, ["article", "topic"]);
  await page.getByRole("link", { name: "October catalog promotion", exact: true }).click();
  await page.waitForURL("**/campaigns/*/edit");
  await page.reload();
  await page.getByRole("combobox", { name: "Product targeting", exact: true }).click();
  await page.getByRole("option", { name: "Selected account products", exact: true }).click();
  await page.getByRole("combobox", { name: "Add account product", exact: true }).click();
  await page.getByRole("option", { name: /API Checker/ }).waitFor();
  assert.equal(await page.getByRole("option", { name: /Rejected product/ }).count(), 0);
  await page.getByPlaceholder("Search account products…").fill("API");
  await page.getByRole("option", { name: /API Checker/ }).click();
  await page.getByRole("combobox", { name: "Campaign audience", exact: true }).click();
  await page.getByRole("option", { name: "Customize audience", exact: true }).click();
  await page.getByLabel("Senior", { exact: true }).uncheck();
  await page.getByLabel("Entry level", { exact: true }).check();
  await page.getByRole("button", { name: "Save campaign", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/campaigns`);
  assert.equal(writes.at(-1).payload.targeting, "selected");
  assert.deepEqual(writes.at(-1).payload.product_ids, [catalogProduct.id]);
  assert.deepEqual(writes.at(-1).payload.audience.experience_levels, ["entry_level"]);
  await page.getByRole("link", { name: "October catalog promotion", exact: true }).click();
  await page.waitForURL("**/campaigns/*/edit");
  await page.reload();
  assert.match(
    await page.getByRole("combobox", { name: "Campaign audience", exact: true }).innerText(),
    /Customize audience/,
  );
  assert.equal(await page.getByLabel("Entry level", { exact: true }).isChecked(), true);
  assert.equal(await page.getByLabel("Senior", { exact: true }).isChecked(), false);
  await page.goto(origin + `/partnerships/accounts/${account.id}/campaigns`);
  await page.getByRole("link", { name: "Create paid campaign", exact: true }).click();
  await page.getByLabel(/Campaign name/).fill("Kubernetes tools agreement");
  await page.getByLabel("Starts", { exact: false }).fill("2026-10-01T00:00");
  await page.getByLabel("Ends", { exact: false }).fill("2026-11-01T00:00");
  await page.getByRole("combobox", { name: "Product targeting", exact: true }).click();
  await page
    .getByRole("option", { name: "Products matching topics or technologies", exact: true })
    .click();
  await page.getByLabel("Technologies", { exact: false }).fill("Kubernetes, PostgreSQL");
  await page.screenshot({ path: root + "reports/partner/campaign-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/campaign-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Create campaign", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/campaigns`);
  assert.equal(writes.at(-1).payload.targeting, "filters");
  assert.deepEqual(writes.at(-1).payload.technologies, ["Kubernetes", "PostgreSQL"]);
  await page.goto(origin + `/partnerships/accounts/${account.id}/edit`);
  await checkAudienceSelection(
    page.getByRole("region", { name: "Audience targeting", exact: true }),
  );
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Platinum", exact: true }).click();
  assert.equal(await page.getByLabel("Senior", { exact: true }).isChecked(), true);
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Gold", exact: true }).click();
  assert.equal(await page.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  await page.goto(origin + `/partnerships/accounts/${account.id}/campaigns`);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  assert.equal(await page.getByRole("link", { name: "Associate asset", exact: true }).count(), 0);
  assert.equal(writes.length, 9);
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
    "Admin account defaults, automatic inventory search/pagination, readable desktop/mobile Products table, catalog inclusion/exclusion, paid campaign targeting and memberships passed; partner cookie denied.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

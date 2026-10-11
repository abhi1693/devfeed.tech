import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import {
  emptyAudience,
  audienceTopicOptions,
  selectedAudience,
  selectAudience,
  checkAudienceSelection,
} from "../../../scripts/testing/audience-targeting.mjs";
import {
  adFormats,
  creativeProduct,
  installCreativeMedia,
} from "../../../scripts/testing/native-ad-formats.mjs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const account = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "API Checker",
  tier: "gold",
  benefits: ["Product placements", "Monthly performance reporting"],
  status: "active",
  effective_promotion_kind: "ad",
  promotion_kind_source: "tier",
  approval_mode: "trusted",
  outbound_destination: "product_url",
  advanced_targeting: false,
  audience: structuredClone(emptyAudience),
  ad_formats: ["feed"],
  allowed_ad_formats: adFormats,
  promotion_surfaces: ["article", "topic", "feed"],
};
const beta = {
  ...account,
  id: "22222222-2222-2222-2222-222222222222",
  name: "BuildKit Studio",
  tier: "bronze",
  benefits: [],
  ad_formats: ["feed"],
  allowed_ad_formats: ["feed"],
};
const previewProduct = {
  id: "44444444-4444-4444-8444-444444444444",
  name: "API Checker product",
  product_url: "https://api-checker.example/?utm_source=devfeed&utm_medium=sponsored",
  listing_url:
    "https://catalog.partner.example/api-checker?utm_source=devfeed&utm_medium=sponsored",
};
const assets = [
  {
    id: "33333333-3333-3333-3333-333333333333",
    name: "API Checker launch campaign",
    kind: "ad",
    product_id: previewProduct.id,
    ad_format: "feed",
    ad_formats: ["feed", "engagement"],
    status: "active",
    impressions: 2400,
    clicks: 120,
    ctr: 5,
    measured_days: 1,
  },
  {
    id: "highest-impressions",
    name: "BuildKit sponsorship",
    kind: "ad",
    product_id: null,
    status: "active",
    impressions: 5000,
    clicks: 1,
    ctr: 0.02,
    measured_days: 1,
  },
  {
    id: "highest-clicks",
    name: "Clicks leader",
    kind: "product",
    product_id: null,
    status: "active",
    impressions: 1000,
    clicks: 500,
    ctr: 50,
    measured_days: 1,
  },
  {
    id: "highest-ctr",
    name: "CTR leader",
    kind: "product",
    product_id: null,
    status: "active",
    impressions: 10,
    clicks: 9,
    ctr: 90,
    measured_days: 1,
  },
  {
    id: "zero",
    name: "Zero delivery",
    kind: "product",
    product_id: null,
    status: "active",
    impressions: 0,
    clicks: 0,
    ctr: null,
    measured_days: 1,
  },
  {
    id: "unmeasured",
    name: "Unmeasured delivery",
    kind: "product",
    product_id: null,
    status: "active",
    impressions: 0,
    clicks: 0,
    ctr: null,
    measured_days: 0,
  },
  ...Array.from({ length: 100 }, (_, index) => ({
    id: `catalog-${index}`,
    name: `Catalog product ${String(index).padStart(3, "0")}`,
    kind: "product",
    product_id: null,
    status: "active",
    impressions: 100 + index,
    clicks: index + 1,
    ctr: (100 * (index + 1)) / (100 + index),
    measured_days: 1,
  })),
];
const dashboardRequests = [];
const preferenceWrites = [];
const previewRequests = [];
const audienceRequests = [];
const productPolicy = { excluded: false, promotion_surfaces: null, ad_formats: null };
const productPolicyWrites = [];
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
  if (path === `/v1/partner/accounts/${account.id}/preferences` && request.method === "PUT") {
    assert.equal(request.headers["x-csrf-token"], "test-csrf");
    assert.equal(request.headers.origin, `http://127.0.0.1:${webPort}`);
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const payload = JSON.parse(raw);
    assert.ok(
      Object.keys(payload).every((key) =>
        ["outbound_destination", "audience", "ad_formats"].includes(key),
      ),
    );
    if (payload.outbound_destination !== undefined)
      assert.ok(["product_url", "listing_url"].includes(payload.outbound_destination));
    preferenceWrites.push(payload);
    if (payload.outbound_destination !== undefined)
      account.outbound_destination = payload.outbound_destination;
    if (payload.audience !== undefined) account.audience = payload.audience;
    if (payload.ad_formats !== undefined) account.ad_formats = payload.ad_formats;
    response.end(JSON.stringify(account));
    return;
  }
  if (path.endsWith("/audience-topics")) {
    assert.equal(request.method, "GET");
    assert.equal(path, `/v1/partner/accounts/${account.id}/audience-topics`);
    audienceRequests.push(Object.fromEntries(url.searchParams));
    response.end(JSON.stringify(audienceTopicOptions(url.searchParams)));
    return;
  }
  if (path.endsWith("/inventory")) {
    assert.equal(request.method, "GET");
    assert.equal(path, `/v1/partner/accounts/${account.id}/inventory`);
    response.end(
      JSON.stringify({
        items: [
          {
            id: previewProduct.id,
            name: previewProduct.name,
            technologies: ["Kubernetes"],
            status: "approved",
            commercial_approved: true,
            approval_source: "qualified",
            globally_excluded: false,
            ...productPolicy,
          },
        ],
        total: 1,
      }),
    );
    return;
  }
  if (path.endsWith(`/products/${previewProduct.id}/promotion`)) {
    assert.equal(
      path,
      `/v1/partner/accounts/${account.id}/products/${previewProduct.id}/promotion`,
    );
    if (request.method === "PUT") {
      assert.equal(request.headers["x-csrf-token"], "test-csrf");
      assert.equal(request.headers.origin, `http://127.0.0.1:${webPort}`);
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const payload = JSON.parse(raw);
      assert.ok(
        Object.keys(payload).every((key) =>
          ["excluded", "promotion_surfaces", "ad_formats"].includes(key),
        ),
      );
      productPolicyWrites.push(payload);
      Object.assign(productPolicy, payload);
    }
    response.end(
      JSON.stringify({ account_id: account.id, product_id: previewProduct.id, ...productPolicy }),
    );
    return;
  }
  if (path.endsWith("/destination-preview")) {
    assert.equal(request.method, "GET");
    assert.equal(request.headers.authorization, undefined);
    const isBeta = path === `/v1/partner/accounts/${beta.id}/destination-preview`;
    if (isBeta && mode !== "superuser") {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    previewRequests.push({ account: isBeta ? beta.id : account.id, mode });
    response.end(JSON.stringify({ product: isBeta ? null : previewProduct }));
    return;
  }
  if (path.endsWith("/creative-preview")) {
    assert.equal(request.method, "GET");
    assert.equal(request.headers.authorization, undefined);
    const isBeta = path === `/v1/partner/accounts/${beta.id}/creative-preview`;
    if (isBeta && mode !== "superuser") {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    response.end(JSON.stringify({ product: isBeta ? null : creativeProduct(previewProduct) }));
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
    const sort = url.searchParams.get("sort") ?? "name";
    const order = url.searchParams.get("order") ?? "asc";
    const offset = Number(url.searchParams.get("offset") ?? 0);
    dashboardRequests.push({ days, sort, order, offset });
    const sortedAssets = assets
      .map((asset, index) =>
        days === "7" && index === 0 ? { ...asset, impressions: 700, clicks: 35 } : asset,
      )
      .sort((left, right) => {
        const leftUnknown = sort !== "name" && (!left.measured_days || left[sort] === null);
        const rightUnknown = sort !== "name" && (!right.measured_days || right[sort] === null);
        if (leftUnknown !== rightUnknown) return leftUnknown ? 1 : -1;
        const comparison =
          sort === "name"
            ? left.name.localeCompare(right.name)
            : leftUnknown
              ? 0
              : left[sort] - right[sort];
        return (order === "asc" ? comparison : -comparison) || left.name.localeCompare(right.name);
      });
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
        assets: isBeta ? [] : sortedAssets.slice(offset, offset + 100),
        asset_total: isBeta ? 0 : assets.length,
        format_totals: isBeta
          ? []
          : [
              {
                ad_format: "feed",
                impressions: days === "7" ? 350 : 1200,
                clicks: days === "7" ? 17 : 60,
                ctr: 5,
                measured_days: 1,
              },
              {
                ad_format: "engagement",
                impressions: days === "7" ? 350 : 1200,
                clicks: days === "7" ? 18 : 60,
                ctr: 5,
                measured_days: 1,
              },
            ],
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
    createRequire(import.meta.url).resolve("next/dist/bin/next"),
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
  await installCreativeMedia(context);
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
  await page.getByText("Sponsored · inherited from tier", { exact: true }).waitFor();
  const settings = page.getByRole("region", { name: "Product promotion settings" });
  assert.equal(await page.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  const preview = page.getByRole("region", { name: "Click destination preview" });
  const previewLink = preview.getByRole("link");
  await preview.getByRole("link", { name: previewProduct.product_url, exact: true }).waitFor();
  assert.equal(await previewLink.getAttribute("href"), previewProduct.product_url);
  assert.equal(await previewLink.innerText(), previewProduct.product_url);
  assert.equal(await preview.getByText(previewProduct.name, { exact: true }).count(), 0);
  assert.equal(await preview.locator("img").count(), 0);
  const selectorBox = await page
    .getByRole("combobox", { name: "Product click destination" })
    .boundingBox();
  const previewBox = await preview.boundingBox();
  assert.ok(
    previewBox.x >= selectorBox.x + selectorBox.width,
    "desktop preview is to the right of destination selector",
  );
  await page.getByRole("combobox", { name: "Product click destination" }).click();
  assert.equal(await page.getByRole("option", { name: "None", exact: true }).count(), 0);
  assert.equal(await page.getByRole("option").count(), 2);
  await page.getByRole("option", { name: "Partner listing", exact: true }).click();
  await preview.getByRole("link", { name: previewProduct.listing_url, exact: true }).waitFor();
  assert.equal(await previewLink.getAttribute("href"), previewProduct.listing_url);
  assert.equal(await previewLink.innerText(), previewProduct.listing_url);
  assert.equal(preferenceWrites.length, 0, "previewing another destination does not save it");
  await settings.screenshot({ path: root + "reports/partner/destination-preview-desktop.png" });
  await page.getByRole("button", { name: "Save click destination", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Click destination saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1), { outbound_destination: "listing_url" });
  await page.reload();
  await page.getByRole("heading", { name: "Gold", exact: true }).waitFor();
  assert.match(
    await page.getByRole("combobox", { name: "Product click destination" }).innerText(),
    /Partner listing/,
  );
  await page.screenshot({ path: root + "reports/partner/overview-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page.getByRole("combobox", { name: "Product click destination" }).click();
  await page.getByRole("option", { name: "Product website", exact: true }).click();
  assert.equal(await previewLink.getAttribute("href"), previewProduct.product_url);
  const mobileSelector = await page
    .getByRole("combobox", { name: "Product click destination" })
    .boundingBox();
  const mobilePreview = await preview.boundingBox();
  assert.ok(mobilePreview.y > mobileSelector.y, "mobile preview stacks below the selector");
  await settings.screenshot({ path: root + "reports/partner/destination-preview-mobile.png" });
  await page.getByRole("button", { name: "Save click destination", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Click destination saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1), { outbound_destination: "product_url" });
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
  account.tier = "silver";
  await page.goto(origin + `/${account.id}`);
  await page.getByRole("heading", { name: "Silver", exact: true }).waitFor();
  const formatSettings = page.getByRole("form", { name: "Ad format settings", exact: true });
  assert.equal(await formatSettings.getByRole("checkbox", { name: /^Feed/ }).isChecked(), true);
  await formatSettings.getByRole("checkbox", { name: /^Homepage sponsorship/ }).check();
  await formatSettings.getByRole("checkbox", { name: /^Engagement/ }).check();
  const destinationBeforeFormats = account.outbound_destination;
  const audienceBeforeFormats = structuredClone(account.audience);
  await formatSettings.getByRole("button", { name: "Save ad formats", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Ad formats saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1), {
    ad_formats: ["feed", "homepage_sponsorship", "engagement"],
  });
  assert.equal(account.outbound_destination, destinationBeforeFormats);
  assert.deepEqual(account.audience, audienceBeforeFormats);
  await page.reload();
  assert.equal(
    await formatSettings.getByRole("checkbox", { name: /^Homepage sponsorship/ }).isChecked(),
    true,
  );
  assert.equal(
    await formatSettings.getByRole("checkbox", { name: /^Engagement/ }).isChecked(),
    true,
  );
  const formatSection = page.getByRole("region", { name: "Automatic ad formats", exact: true });
  await formatSection.locator("summary").filter({ hasText: "Creative preview" }).click();
  const actualCreative = formatSection.getByRole("region", {
    name: "Creative preview",
    exact: true,
  });
  await actualCreative.getByText(previewProduct.name, { exact: true }).waitFor();
  await actualCreative.locator("img").evaluate((img) => img.decode());
  assert.equal(await actualCreative.getByText("Sponsored", { exact: true }).count(), 1);
  assert.equal(await actualCreative.getByText("Visit website", { exact: true }).count(), 0);
  assert.equal(await preview.locator("img").count(), 0, "destination preview remains URL-only");
  await page.setViewportSize({ width: 1280, height: 1100 });
  await formatSection.screenshot({ path: root + "reports/partner/ad-formats-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await formatSection.screenshot({ path: root + "reports/partner/ad-formats-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 1100 });
  account.tier = "gold";
  await page.goto(origin + `/${account.id}/performance`);
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
  // A saved page may no longer exist after assets are removed from the account.
  await page.goto(
    `http://127.0.0.1:${webPort}/${account.id}/assets?days=30&sort=name&order=asc&asset_offset=200`,
  );
  await page.getByText("API Checker launch campaign", { exact: true }).waitFor();
  await page.waitForURL((url) => !url.searchParams.has("asset_offset"));
  assert.deepEqual(dashboardRequests.at(-1), {
    days: "30",
    sort: "name",
    order: "asc",
    offset: 0,
  });
  assert.equal(await page.getByRole("columnheader", { name: "Type", exact: true }).count(), 0);
  await page.getByRole("button", { name: "Manage product placements", exact: true }).click();
  const productCatalog = page.getByRole("dialog", {
    name: "Imported product placements",
    exact: true,
  });
  await productCatalog
    .getByRole("button", { name: `Edit placements for ${previewProduct.name}`, exact: true })
    .click();
  const productDialog = page.getByRole("dialog", {
    name: `${previewProduct.name} placements`,
    exact: true,
  });
  await productDialog.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Choose product formats", exact: true }).click();
  for (const name of ["Feed", "Homepage sponsorship", "Sponsored content"])
    await productDialog.getByRole("checkbox", { name, exact: true }).uncheck();
  await productDialog.getByRole("checkbox", { name: "Engagement", exact: true }).check();
  await productDialog.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Choose product placements", exact: true }).click();
  await productDialog.getByLabel("Article pages", { exact: true }).uncheck();
  await productDialog.getByLabel("Topic and category pages", { exact: true }).uncheck();
  await productDialog.getByLabel("Reader feeds", { exact: true }).check();
  await productDialog.screenshot({ path: root + "reports/partner/product-placements-mobile.png" });
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.deepEqual(productPolicyWrites.at(-1), {
    ad_formats: ["engagement"],
    promotion_surfaces: ["feed"],
  });
  await page.keyboard.press("Escape");
  await productCatalog.waitFor({ state: "hidden" });
  await page.reload();
  await page
    .getByRole("button", { name: "Edit placements for API Checker launch campaign", exact: true })
    .click();
  const reportProductDialog = page.getByRole("dialog", {
    name: "API Checker launch campaign placements",
    exact: true,
  });
  assert.equal(
    await reportProductDialog
      .getByRole("checkbox", { name: "Engagement", exact: true })
      .isChecked(),
    true,
  );
  assert.equal(
    await reportProductDialog.getByRole("checkbox", { name: "Feed", exact: true }).isChecked(),
    false,
  );
  await page.setViewportSize({ width: 1280, height: 1100 });
  await reportProductDialog.screenshot({
    path: root + "reports/partner/product-placements-desktop.png",
  });
  await reportProductDialog.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account formats", exact: true }).click();
  await reportProductDialog
    .getByRole("combobox", { name: "Product placements", exact: true })
    .click();
  await page.getByRole("option", { name: "Inherit account placements", exact: true }).click();
  await reportProductDialog
    .getByRole("button", { name: "Save product placements", exact: true })
    .click();
  await reportProductDialog.waitFor({ state: "hidden" });
  assert.deepEqual(productPolicyWrites.at(-1), { ad_formats: null, promotion_surfaces: null });
  assert.equal(
    await page
      .getByRole("button", { name: "Sort by Name", exact: true })
      .locator("..")
      .getAttribute("aria-sort"),
    "ascending",
  );
  await page.setViewportSize({ width: 1440, height: 1100 });
  async function sortAssets(label, sort, order, firstName) {
    const header = page.getByRole("button", { name: `Sort by ${label}`, exact: true });
    await header.click();
    await page.waitForURL(
      (url) =>
        url.searchParams.get("sort") === sort &&
        url.searchParams.get("order") === order &&
        !url.searchParams.has("asset_offset"),
    );
    await page.waitForFunction(
      (name) => document.querySelector("tbody tr [data-asset-name]")?.textContent === name,
      firstName,
    );
    await page.locator('section[aria-busy="false"]').waitFor();
    assert.equal(
      await header.locator("..").getAttribute("aria-sort"),
      order === "asc" ? "ascending" : "descending",
    );
    assert.equal(await header.evaluate((element) => document.activeElement === element), true);
    assert.deepEqual(dashboardRequests.at(-1), { days: "30", sort, order, offset: 0 });
  }
  await sortAssets("Name", "name", "desc", "Zero delivery");
  await sortAssets("Name", "name", "asc", "API Checker launch campaign");
  await sortAssets("Impressions", "impressions", "desc", "BuildKit sponsorship");
  await sortAssets("Impressions", "impressions", "asc", "Zero delivery");
  await sortAssets("Clicks", "clicks", "desc", "Clicks leader");
  await sortAssets("Clicks", "clicks", "asc", "Zero delivery");
  await sortAssets("CTR", "ctr", "desc", "CTR leader");
  await sortAssets("CTR", "ctr", "asc", "BuildKit sponsorship");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get("asset_offset") === "100");
  await page.getByText("Unmeasured delivery", { exact: true }).waitFor();
  assert.equal(await page.locator("tbody tr").count(), 6);
  assert.deepEqual(
    await page
      .locator("tbody tr")
      .allTextContents()
      .then((rows) =>
        rows
          .slice(-2)
          .map((row) => row.includes("Unmeasured delivery") || row.includes("Zero delivery")),
      ),
    [true, true],
  );
  await page.reload();
  await page.getByText("Unmeasured delivery", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "Sort by CTR", exact: true })
      .locator("..")
      .getAttribute("aria-sort"),
    "ascending",
  );
  assert.equal(new URL(page.url()).searchParams.get("asset_offset"), "100");
  await sortAssets("Impressions", "impressions", "desc", "BuildKit sponsorship");
  await page.screenshot({
    path: root + "reports/partner/assets-sorting-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByText("Unmeasured delivery", { exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("BuildKit sponsorship", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.has("asset_offset"), false);
  assert.deepEqual(dashboardRequests.at(-1), {
    days: "7",
    sort: "impressions",
    order: "desc",
    offset: 0,
  });
  await page.getByRole("link", { name: "Performance", exact: true }).click();
  await page.getByRole("heading", { name: "Daily activity" }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("sort"), "impressions");
  assert.equal(new URL(page.url()).searchParams.get("order"), "desc");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page.getByRole("link", { name: "Products & ads", exact: true }).click();
  await page.getByText("BuildKit sponsorship", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "Sort by Impressions", exact: true })
      .locator("..")
      .getAttribute("aria-sort"),
    "descending",
  );
  await page.getByRole("button", { name: "Sort by CTR", exact: true }).click();
  await page.getByText("CTR leader", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("sort"), "ctr");
  assert.equal(new URL(page.url()).searchParams.get("order"), "desc");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/assets-sorting-mobile.png",
    fullPage: true,
  });
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
  account.tier = "platinum";
  account.advanced_targeting = true;
  await page.goto(origin + `/${account.id}`);
  await page.getByRole("heading", { name: "Platinum", exact: true }).waitFor();
  const audience = page.getByRole("region", { name: "Audience targeting", exact: true });
  await selectAudience(page, audience, audienceRequests);
  await audience.screenshot({
    path: root + "reports/partner/audience-portal-desktop.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await audience.screenshot({
    path: root + "reports/partner/audience-portal-mobile.png",
    animations: "disabled",
  });
  const destinationBeforeAudience = account.outbound_destination;
  await page.getByRole("button", { name: "Save audience", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Audience saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1), { audience: selectedAudience });
  assert.equal(account.outbound_destination, destinationBeforeAudience);
  await page.reload();
  await checkAudienceSelection(audience);
  assert.ok(
    audienceRequests.some((params) => params.topic_id === selectedAudience.language_topic_ids[0]),
    "Reload resolves saved selections by ID",
  );
  account.tier = "diamond";
  await page.reload();
  await page.getByRole("heading", { name: "Diamond", exact: true }).waitFor();
  await checkAudienceSelection(audience);
  account.tier = "gold";
  account.advanced_targeting = false;
  await page.reload();
  await page.getByRole("heading", { name: "Gold", exact: true }).waitFor();
  assert.equal(await audience.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await audience.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  await audience
    .getByRole("list", { name: "Selected programming languages", exact: true })
    .getByText("Python", { exact: true })
    .waitFor();
  assert.deepEqual(
    account.audience,
    selectedAudience,
    "A downgraded account retains restrictions without widening them",
  );
  account.audience = structuredClone(emptyAudience);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await context.addCookies([{ name: "devfeed_partner_session", value: "superuser", url: origin }]);
  await page.goto(origin);
  assert.equal(await page.getByRole("link", { name: "Manage partnerships" }).count(), 0);
  assert.equal(await page.getByRole("heading", { name: "Manage partnerships" }).count(), 0);
  await page.getByRole("combobox", { name: "Partner account" }).click();
  await page.getByRole("option", { name: "BuildKit Studio", exact: true }).click();
  await page.getByRole("heading", { name: "Bronze", exact: true }).waitFor();
  await page
    .getByText("A preview will appear when this account has an accepted product.", { exact: true })
    .waitFor();
  assert.equal(await preview.getByRole("link").count(), 0);
  assert.ok(
    previewRequests.some((request) => request.account === beta.id && request.mode === "superuser"),
  );
  assert.equal(new URL(page.url()).pathname, `/${beta.id}`);
  assert.equal(new URL(page.url()).searchParams.has("account"), false);
  for (const path of [
    "/performance",
    "/assets",
    `/?account=${beta.id}`,
    `/${beta.id}/performance?account=${account.id}`,
  ]) {
    const removedRoute = await page.goto(origin + path);
    assert.equal(removedRoute.status(), 404, path);
  }
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
    "Partner account-wide promotion policy and click destination saving, reporting sorting, pagination, URL/reload/navigation persistence, desktop/mobile layouts, role boundaries, and empty states passed.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => mock.close(resolve));
}

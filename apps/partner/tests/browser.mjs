import assert from "node:assert/strict";
import { createServer, request as httpRequest } from "node:http";
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

import { createConnectionFixture, assertConnectionJourneys } from "./browser-connections.mjs";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const account = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "API Checker",
  tier: "gold",
  benefits: ["Product placements", "Monthly performance reporting"],
  status: "active",
  organic_enabled: true,
  auto_include_products: true,
  promotion_kind: null,
  allowed_promotion_kinds: ["product", "ad"],
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
  id: "22222222-2222-4222-8222-222222222222",
  name: "BuildKit Studio",
  tier: "bronze",
  effective_promotion_kind: "product",
  benefits: [],
  ad_formats: ["feed"],
  allowed_ad_formats: ["feed"],
};
const manyAccounts = Array.from({ length: 105 }, (_, index) => ({
  ...beta,
  id: `55555555-5555-4555-8555-${String(index + 1).padStart(12, "0")}`,
  name: `Catalog partner ${String(index + 1).padStart(3, "0")}`,
}));
const bookmarkedAccount = manyAccounts[104];
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
// Reporting contracts use real UUID identities, including synthetic pagination records.
assets.forEach((asset, index) => {
  asset.id = `66666666-6666-4666-8666-${String(index + 1).padStart(12, "0")}`;
});
const dashboardRequests = [];
const workspaceDashboardRequests = [];
const accountPages = [];
const accountLookups = [];
const logoutRequests = [];
const preferenceWrites = [];
const previewRequests = [];
const creativePreviewRequests = [];
const audienceRequests = [];
const productPolicy = { excluded: false, promotion_surfaces: null, ad_formats: null };
const productPolicyWrites = [];
const connectionFixture = createConnectionFixture({ account, origin: () => origin });
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
    logoutRequests.push({ method: request.method, mode });
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
        issuer: "https://identity.example",
        organization_id: "devfeed",
        expires_at: 4_102_444_800,
        name: "Alex Partner",
        roles: mode === "superuser" ? ["partner", "superuser"] : ["partner"],
        csrf_token: "test-csrf",
      }),
    );
    return;
  }
  const memberAccounts =
    mode === "empty"
      ? []
      : mode === "many"
        ? [...manyAccounts].sort((left, right) => left.name.localeCompare(right.name))
        : mode === "superuser"
          ? [account, beta]
          : [account];
  if (await connectionFixture.handle(request, response, url, memberAccounts)) return;
  if (path === "/v1/partner/accounts") {
    if (request.method === "POST") {
      response.writeHead(201);
      response.end(JSON.stringify(account));
      return;
    }
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 100);
    assert.ok(limit > 0 && limit <= 100, "Account records use bounded pages");
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const matches = memberAccounts.filter((item) => item.name.toLowerCase().includes(query));
    const items = matches.slice(offset, offset + limit);
    accountPages.push({ mode, offset, limit, ids: items.map((item) => item.id) });
    response.end(
      JSON.stringify({
        items,
        total: matches.length,
      }),
    );
    return;
  }
  const accountLookup = /^\/v1\/partner\/accounts\/([a-f0-9-]+)$/i.exec(path);
  if (accountLookup && request.method === "GET") {
    const selected = memberAccounts.find((item) => item.id === accountLookup[1]);
    accountLookups.push({ mode, id: accountLookup[1], authorized: Boolean(selected) });
    if (!selected) response.writeHead(404);
    response.end(JSON.stringify(selected ?? {}));
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
  const accountInventory = /^\/v1\/partner\/accounts\/([a-f0-9-]+)\/inventory$/i.exec(path);
  if (path === "/v1/partner/inventory" || accountInventory) {
    assert.equal(request.method, "GET");
    const selectedId = accountInventory?.[1] ?? url.searchParams.get("account_id");
    if (selectedId && !memberAccounts.some((item) => item.id === selectedId)) {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    const owner = mode === "many" ? bookmarkedAccount : account;
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const productId = url.searchParams.get("product_id");
    const items =
      memberAccounts.some((item) => item.id === owner.id) &&
      (!selectedId || selectedId === owner.id) &&
      (!productId || productId === previewProduct.id) &&
      previewProduct.name.toLowerCase().includes(query)
        ? [
            {
              id: previewProduct.id,
              account_id: owner.id,
              account_name: owner.name,
              name: previewProduct.name,
              technologies: ["Kubernetes"],
              status: "approved",
              commercial_approved: true,
              approval_source: "qualified",
              globally_excluded: false,
              ...productPolicy,
            },
          ]
        : [];
    response.end(
      JSON.stringify({
        items,
        total: items.length,
        limit: Number(url.searchParams.get("limit") ?? 100),
        offset: Number(url.searchParams.get("offset") ?? 0),
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
    const productId = url.searchParams.get("product_id");
    if (productId) assert.equal(productId, previewProduct.id);
    creativePreviewRequests.push({ productId, excluded: productPolicy.excluded });
    response.end(
      JSON.stringify({
        product: isBeta || productPolicy.excluded ? null : creativeProduct(previewProduct),
      }),
    );
    return;
  }
  if (path === "/v1/partner/dashboard") {
    const selectedId = url.searchParams.get("account_id");
    if (selectedId && !memberAccounts.some((item) => item.id === selectedId)) {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    const scope = selectedId
      ? memberAccounts.filter((item) => item.id === selectedId)
      : memberAccounts;
    const days = url.searchParams.get("days");
    const sort = url.searchParams.get("sort") ?? "name";
    const order = url.searchParams.get("order") ?? "asc";
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 25);
    assert.equal(limit, 25, "Performance uses the same bounded page size as CRUD lists");
    dashboardRequests.push({ days, sort, order, offset });
    workspaceDashboardRequests.push({ mode, accountIds: scope.map((item) => item.id) });
    const impressions = scope.reduce(
      (total, item) =>
        total +
        (mode === "many"
          ? 8100 + manyAccounts.indexOf(item)
          : item.id === account.id
            ? days === "7"
              ? 700
              : 2400
            : 600),
      0,
    );
    const clicks = scope.reduce(
      (total, item) =>
        total + (mode === "many" ? 10 : item.id === account.id ? (days === "7" ? 35 : 120) : 6),
      0,
    );
    const measured = scope.length > 0;
    const owner = mode === "many" ? bookmarkedAccount : account;
    const scopedAssets = scope.some((item) => item.id === owner.id)
      ? assets.map((asset, index) => ({
          ...asset,
          ...(days === "7" && index === 0 ? { impressions: 700, clicks: 35 } : {}),
          account_id: owner.id,
          account_name: owner.name,
        }))
      : [];
    const sortedAssets = scopedAssets.sort((left, right) => {
      const leftUnknown =
        !["name", "account"].includes(sort) && (!left.measured_days || left[sort] === null);
      const rightUnknown =
        !["name", "account"].includes(sort) && (!right.measured_days || right[sort] === null);
      if (leftUnknown !== rightUnknown) return leftUnknown ? 1 : -1;
      const comparison =
        sort === "name"
          ? left.name.localeCompare(right.name)
          : sort === "account"
            ? left.account_name.localeCompare(right.account_name)
            : leftUnknown
              ? 0
              : left[sort] - right[sort];
      return (order === "asc" ? comparison : -comparison) || left.name.localeCompare(right.name);
    });
    response.end(
      JSON.stringify({
        account_total: scope.length,
        start: days === "7" ? "2026-10-01" : "2026-09-08",
        end: "2026-10-07",
        totals: {
          impressions,
          clicks,
          ctr: impressions ? (100 * clicks) / impressions : null,
          measured_days: measured ? 1 : 0,
        },
        last_updated_at: measured ? "2026-10-07T10:00:00Z" : null,
        trend: measured ? [{ day: "2026-10-07", impressions, clicks }] : [],
        assets: sortedAssets.slice(offset, offset + limit),
        asset_total: sortedAssets.length,
        format_totals: measured
          ? [
              {
                ad_format: "feed",
                impressions,
                clicks,
                ctr: (100 * clicks) / impressions,
                measured_days: 1,
              },
            ]
          : [],
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

async function checkUploadDeadline() {
  const completedLogouts = logoutRequests.length;
  let upload;
  let watchdog;
  try {
    const result = await new Promise((resolve, reject) => {
      const started = performance.now();
      watchdog = setTimeout(() => reject(new Error("Incomplete upload exceeded 7 seconds")), 7000);
      upload = httpRequest(
        origin + "/api/v1/partner/auth/logout",
        {
          method: "POST",
          agent: false,
          headers: {
            Origin: origin,
            Cookie: "devfeed_partner_session=partner",
            "Content-Type": "application/json",
            "X-CSRF-Token": "test-csrf",
          },
        },
        (response) => {
          let body = "";
          response.setEncoding("utf8");
          response.on("data", (chunk) => {
            body += chunk;
          });
          response.once("error", reject);
          response.once("aborted", () => reject(new Error("Deadline response was interrupted")));
          response.once("end", () => {
            resolve({
              status: response.statusCode,
              headers: response.headers,
              body,
              elapsedMs: performance.now() - started,
              uploadEnded: upload.writableEnded,
            });
          });
        },
      );
      upload.once("error", reject);
      // Deliberately leave the chunked request open after its first body chunk.
      upload.write('{"private-slow-upload-marker":');
    });
    assert.equal(result.status, 408);
    assert.deepEqual(JSON.parse(result.body), { detail: "Incomplete request" });
    assert.ok(
      String(result.headers["cache-control"])
        .split(",")
        .map((directive) => directive.trim())
        .includes("no-store"),
    );
    assert.equal(
      result.uploadEnded,
      false,
      "The timeout response does not wait for upload completion",
    );
    assert.ok(
      result.elapsedMs >= 1800 && result.elapsedMs < 6000,
      `The 2-second body deadline responded after ${Math.round(result.elapsedMs)}ms`,
    );
    assert.equal(
      logoutRequests.length,
      completedLogouts,
      "Incomplete uploads never reach the partner API",
    );
    console.log(
      `Partner incomplete-upload deadline returned 408 after ${Math.round(result.elapsedMs)}ms.`,
    );
  } finally {
    clearTimeout(watchdog);
    upload?.destroy();
  }
}

let browser;
let activePage;
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      if ((await fetch(origin + "/login")).ok) break;
    } catch {}
    if (attempt === 99) throw new Error("Portal failed to start: " + logs);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  await checkUploadDeadline();
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await installCreativeMedia(context);
  const page = await context.newPage();
  activePage = page;
  const errors = [];
  page.on("dialog", () => {
    errors.push("Unexpected native dialog");
  });
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
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  await page.getByRole("figure", { name: "Impressions", exact: true }).waitFor();
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
  assert.equal(await page.getByRole("combobox", { name: "Reporting period" }).count(), 1);
  assert.equal(await page.getByRole("combobox", { name: "Product click destination" }).count(), 0);
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.getByRole("figure", { name: "Impressions", exact: true }).waitFor();
  await page.screenshot({ path: root + "reports/partner/dashboard-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/dashboard-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(origin + `/settings/${account.id}`);
  await page.getByRole("region", { name: "Product promotion settings" }).waitFor();
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
  await page.getByText("Gold", { exact: true }).waitFor();
  assert.match(
    await page.getByRole("combobox", { name: "Product click destination" }).innerText(),
    /Partner listing/,
  );
  await page.screenshot({ path: root + "reports/partner/settings-desktop.png", fullPage: true });
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
  await page.screenshot({ path: root + "reports/partner/settings-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "User menu: Alex Partner" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.getByRole("menu").evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
  });
  await page.screenshot({ path: root + "reports/partner/avatar-menu-mobile.png", fullPage: true });
  await page.getByRole("menuitem", { name: "Dashboard", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/");
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "User menu: Alex Partner" })
      .getAttribute("aria-expanded"),
    "false",
  );
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Performance", exact: true })
    .click();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  account.tier = "silver";
  await page.goto(origin + `/settings/${account.id}`);
  await page.getByText("Silver", { exact: true }).waitFor();
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
  await page.goto(origin + `/performance`);
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page
    .getByRole("region", { name: "Impressions", exact: true })
    .getByText("2,400", { exact: true })
    .waitFor();
  assert.equal(await page.getByRole("figure").count(), 2);
  await page.getByRole("link", { name: "Export daily CSV", exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 30 days" }).click();
  await page.getByText("2,400", { exact: true }).first().waitFor();
  await mkdir(root + "reports/partner", { recursive: true });
  await page
    .getByRole("region", { name: "Ad performance", exact: true })
    .screenshot({ path: root + "reports/partner/performance-charts-desktop.png" });
  await page.screenshot({ path: root + "reports/partner/desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Products", exact: true })
    .click();
  await page.waitForURL((url) => url.pathname === `/products`);
  await page.getByRole("table", { name: "Imported products" }).waitFor();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Manage product placements" }).count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/products-mobile.png", fullPage: true });
  await page.reload();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).click();
  const productPath = `/products/${previewProduct.id}`;
  await page.waitForURL((url) => url.pathname === productPath);
  await page.getByRole("heading", { name: previewProduct.name, exact: true, level: 1 }).waitFor();
  await page
    .getByRole("region", { name: "Creative preview", exact: true })
    .getByText(previewProduct.name, { exact: true })
    .waitFor();
  assert.deepEqual(creativePreviewRequests.at(-1), {
    productId: previewProduct.id,
    excluded: false,
  });
  const directProduct = await page.goto(origin + productPath);
  assert.equal(
    directProduct.status(),
    200,
    "A product bookmark resolves its authorized account policy without an account URL prefix",
  );
  await page.getByRole("heading", { name: previewProduct.name, exact: true, level: 1 }).waitFor();
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `${productPath}/placements`);
  await page
    .getByRole("heading", { name: `Edit ${previewProduct.name} placements`, exact: true })
    .waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Exclude this product", exact: true }).click();
  await page.getByText("Unsaved changes", { exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Choose product formats", exact: true }).click();
  for (const name of ["Feed", "Homepage sponsorship", "Sponsored content"])
    await page.getByRole("checkbox", { name, exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Engagement", exact: true }).check();
  await page.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Choose product placements", exact: true }).click();
  await page.getByLabel("Article pages", { exact: true }).uncheck();
  await page.getByLabel("Topic and category pages", { exact: true }).uncheck();
  await page.getByLabel("Reader feeds", { exact: true }).check();
  await page.screenshot({
    path: root + "reports/partner/product-placements-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.deepEqual(productPolicyWrites.at(-1), {
    excluded: true,
    ad_formats: ["engagement"],
    promotion_surfaces: ["feed"],
  });
  await page.reload();
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByText(/This product is excluded/).waitFor();
  await page.getByText(/This product is unavailable for preview/).waitFor();
  assert.deepEqual(creativePreviewRequests.at(-1), {
    productId: previewProduct.id,
    excluded: true,
  });
  await page.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Allow this product", exact: true }).click();
  await page.getByText("Unsaved changes", { exact: true }).waitFor();
  assert.equal(productPolicy.excluded, true, "Draft Allow does not restore delivery before save");
  assert.equal(
    await page.getByRole("checkbox", { name: "Engagement", exact: true }).isChecked(),
    true,
  );
  assert.equal(await page.getByRole("checkbox", { name: "Feed", exact: true }).isChecked(), false);
  await page.setViewportSize({ width: 1280, height: 1100 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/product-placements-desktop.png",
    fullPage: true,
  });
  await page.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account formats", exact: true }).click();
  await page.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account placements", exact: true }).click();
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.deepEqual(productPolicyWrites.at(-1), {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
  });
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Exclude this product", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.equal(productPolicy.excluded, false, "Cancel discards an unsaved draft without a popup");
  const previousBookmark = await page.goto(origin + `/${account.id}/assets?days=30`);
  assert.equal(previousBookmark.status(), 404, "Obsolete account-prefixed routes are removed");
  await page.goto(origin + "/products");
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.screenshot({ path: root + "reports/partner/products-desktop.png", fullPage: true });
  await assertConnectionJourneys({
    page,
    origin,
    account,
    fixture: connectionFixture,
    artifactDir: root + "reports/partner",
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  // Removed report pages reset independently of the Products inventory list.
  await page.goto(origin + `/performance?days=30&sort=name&order=asc&asset_offset=200`);
  await page.getByText("API Checker launch campaign", { exact: true }).waitFor();
  await page.waitForURL((url) => !url.searchParams.has("asset_offset"));
  assert.deepEqual(dashboardRequests.at(-1), { days: "30", sort: "name", order: "asc", offset: 0 });
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
  for (const offset of [25, 50, 75, 100]) {
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.waitForURL((url) => url.searchParams.get("asset_offset") === String(offset));
    await page.locator('section[aria-busy="false"]').waitFor();
  }
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
  for (const offset of [25, 50, 75, 100]) {
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.waitForURL((url) => url.searchParams.get("asset_offset") === String(offset));
    await page.locator('section[aria-busy="false"]').waitFor();
  }
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
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Performance", exact: true })
    .click();
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("sort"), "impressions");
  assert.equal(new URL(page.url()).searchParams.get("order"), "desc");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Products", exact: true })
    .click();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await page.getByRole("button", { name: "Navigation" }).click();
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Performance", exact: true })
    .click();
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
  await page
    .getByRole("region", { name: "Ad performance", exact: true })
    .screenshot({ path: root + "reports/partner/performance-charts-mobile.png" });
  await page.screenshot({
    path: root + "reports/partner/assets-sorting-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Performance", exact: true })
    .click();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, `/performance`);
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.reload();
  assert.match(
    await page.getByRole("combobox", { name: "Reporting period" }).innerText(),
    /Last 7 days/,
  );
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Dashboard", exact: true })
    .click();
  await page.waitForURL((url) => url.pathname === "/");
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
  await page.goBack();
  await page.waitForURL("**/performance?*");
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  account.tier = "platinum";
  account.advanced_targeting = true;
  await page.goto(origin + `/settings/${account.id}`);
  await page.getByText("Platinum", { exact: true }).waitFor();
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
  await page.getByText("Diamond", { exact: true }).waitFor();
  await checkAudienceSelection(audience);
  account.tier = "gold";
  account.advanced_targeting = false;
  await page.reload();
  await page.getByText("Gold", { exact: true }).waitFor();
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
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/", "The workspace has no account UUID prefix");
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page
    .getByRole("region", { name: "Impressions", exact: true })
    .getByText("3,000", { exact: true })
    .waitFor();
  assert.deepEqual(workspaceDashboardRequests.at(-1).accountIds, [account.id, beta.id]);
  await page.goto(origin + "/performance");
  await page.getByRole("columnheader", { name: /account/i }).waitFor();
  await page
    .getByRole("region", { name: "Impressions", exact: true })
    .getByText("3,000", { exact: true })
    .waitFor();
  await page
    .getByRole("region", { name: "Click-through rate", exact: true })
    .getByText("4.20%", { exact: true })
    .waitFor();
  await page.goto(origin + "/products");
  await page.getByRole("columnheader", { name: /account/i }).waitFor();
  await page
    .getByRole("table", { name: "Imported products" })
    .getByText(account.name, { exact: true })
    .waitFor();
  await page.goto(origin + "/settings");
  await page
    .getByRole("table", { name: "Partner accounts" })
    .getByText(beta.name, { exact: true })
    .waitFor();
  await page.screenshot({
    path: root + "reports/partner/workspace-settings-desktop.png",
    fullPage: true,
  });
  await page.goto(origin + `/settings/${beta.id}`);
  await page.getByText("Bronze", { exact: true }).waitFor();
  await page
    .getByText("A preview will appear when this account has an accepted product.", { exact: true })
    .waitFor();
  assert.equal(await preview.getByRole("link").count(), 0);
  assert.ok(
    previewRequests.some((request) => request.account === beta.id && request.mode === "superuser"),
  );
  assert.equal(new URL(page.url()).pathname, `/settings/${beta.id}`);
  for (const path of [
    "/assets",
    `/${account.id}`,
    `/${beta.id}/performance`,
    `/${account.id}/products`,
    `/${account.id}/settings`,
  ]) {
    const removedRoute = await page.goto(origin + path);
    assert.equal(removedRoute.status(), 404, path);
  }
  const foreign = await page.goto(origin + "/foreign/performance");
  assert.equal(foreign.status(), 404);
  const removed = await page.goto(origin + "/management");
  assert.equal(removed.status(), 404);

  await context.addCookies([{ name: "devfeed_partner_session", value: "many", url: origin }]);
  await page.goto(origin + "/performance?days=7&sort=clicks&order=desc");
  const manyTotal = manyAccounts.reduce((total, _, index) => total + 8100 + index, 0);
  await page
    .getByRole("region", { name: "Impressions", exact: true })
    .getByText(manyTotal.toLocaleString("en"), { exact: true })
    .waitFor();
  assert.equal(
    workspaceDashboardRequests.at(-1).accountIds.length,
    105,
    "Workspace reporting includes memberships beyond the first page",
  );
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  assert.equal(new URL(page.url()).pathname, "/performance");
  await page.goto(origin + "/products");
  await page
    .getByRole("table", { name: "Imported products" })
    .getByText(bookmarkedAccount.name, { exact: true })
    .waitFor();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await page.goto(origin + "/settings");
  const accountTable = page.getByRole("table", { name: "Partner accounts" });
  const accountPagination = page.getByRole("navigation", { name: "Account pages", exact: true });
  await accountPagination.getByText("1–25 of 105", { exact: true }).waitFor();
  assert.equal(await accountTable.locator("tbody tr").count(), 25);
  assert.equal(await accountTable.getByText(bookmarkedAccount.name, { exact: true }).count(), 0);
  for (let offset = 25; offset <= 100; offset += 25) {
    await accountPagination.getByRole("button", { name: "Next accounts", exact: true }).click();
    await accountPagination
      .getByText(`${offset + 1}–${Math.min(offset + 25, 105)} of 105`, { exact: true })
      .waitFor();
    await accountTable.getByText(manyAccounts[offset].name, { exact: true }).waitFor();
  }
  assert.equal(await accountTable.locator("tbody tr").count(), 5);
  await accountTable.getByText(bookmarkedAccount.name, { exact: true }).waitFor();
  await page.reload();
  await accountPagination.getByText("101–105 of 105", { exact: true }).waitFor();
  const bookmark = `/settings/${bookmarkedAccount.id}`;
  const bookmarkedResponse = await page.goto(origin + bookmark);
  assert.equal(
    bookmarkedResponse.status(),
    200,
    "Record membership is independent of account-list pagination",
  );
  await page.getByText("Bronze", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, bookmark);
  assert.equal(new URL(page.url()).searchParams.has("account_offset"), false);
  bookmarkedAccount.name = "Catalog partner 103A";
  const reorderedResponse = await page.goto(origin + bookmark);
  assert.equal(
    reorderedResponse.status(),
    200,
    "Renaming accounts does not invalidate record bookmarks",
  );
  await page.getByText("Bronze", { exact: true }).waitFor();
  await page.reload();
  await page.getByText("Bronze", { exact: true }).waitFor();
  assert.ok(
    accountLookups.some(
      (request) =>
        request.mode === "many" && request.id === bookmarkedAccount.id && request.authorized,
    ),
  );
  const manyPages = accountPages.filter((request) => request.mode === "many");
  assert.ok(manyPages.some((request) => request.offset === 100 && request.ids.length === 5));
  assert.ok(manyPages.every((request) => request.ids.length <= 100));
  const foreignAccountId = "99999999-9999-4999-8999-999999999999";
  const unauthorized = await page.goto(origin + `/settings/${foreignAccountId}`);
  assert.equal(unauthorized.status(), 404, "Foreign account records remain inaccessible");
  assert.ok(
    accountLookups.some(
      (request) =>
        request.mode === "many" && request.id === foreignAccountId && !request.authorized,
    ),
  );
  const filteredForeign = await context.request.get(
    origin + `/api/v1/partner/dashboard?account_id=${foreignAccountId}`,
  );
  assert.equal(
    filteredForeign.status(),
    404,
    "An account filter cannot expand the membership scope",
  );
  assert.equal(
    workspaceDashboardRequests.some((request) => request.accountIds.includes(foreignAccountId)),
    false,
  );

  await context.addCookies([{ name: "devfeed_partner_session", value: "empty", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "No partner accounts yet" }).waitFor();
  const completedLogouts = logoutRequests.length;
  await page.getByRole("button", { name: "User menu: Alex Partner" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
  assert.equal(
    logoutRequests.length,
    completedLogouts + 1,
    "Normal logout still reaches the partner API",
  );
  assert.deepEqual(logoutRequests.at(-1), { method: "POST", mode: "empty" });
  await context.addCookies([{ name: "devfeed_partner_session", value: "denied", url: origin }]);
  await page.goto(origin);
  await page.waitForURL("**/login?error=access_denied");
  await page.getByText("Your account needs the partner role to access this portal.").waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "Partner dashboard charts, full-page product and connection CRUD, secure credential controls, settings, report sorting, all-membership aggregation, paginated account records, clean URLs, mobile layouts, role boundaries, and empty states passed.",
  );
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    console.error("Failed partner journey URL:", activePage.url());
    console.error((await activePage.locator("body").innerText()).slice(0, 3000));
    await activePage.screenshot({
      path: root + "reports/partner/journey-failure.png",
      fullPage: true,
    });
  }
  throw error;
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => mock.close(resolve));
}

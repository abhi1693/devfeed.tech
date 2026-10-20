import { checkAppIcons } from "../../../scripts/testing/app-icons.mjs";
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
  checkHeaderSponsorshipPreview,
} from "../../../scripts/testing/native-ad-formats.mjs";

import { createConnectionFixture, assertConnectionJourneys } from "./browser-connections.mjs";
import { createManualProductFixture, assertManualProductJourneys } from "./browser-products.mjs";
import { assertPartnerThemeJourneys } from "./browser-theme.mjs";
import { assertPartnerRecoveryJourneys } from "./browser-recovery.mjs";
const manualProducts = createManualProductFixture();

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
  link_rel: ["nofollow"],
  configurable_link_rel: false,
  link_rel_options: ["nofollow", "sponsored", "ugc", "external"],
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
  tagline: "Monitor production APIs and diagnose reliability problems.",
  product_url: "https://api-checker.example/?utm_source=devfeed&utm_medium=sponsored",
  listing_url:
    "https://catalog.partner.example/api-checker?utm_source=devfeed&utm_medium=sponsored",
};
const productReviewReason =
  "The product supports a concrete software development workflow: monitoring production APIs, investigating failed requests, and diagnosing service reliability. Its documentation demonstrates integration with Kubernetes services and explains how developers can use those results to resolve production incidents.";
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
let reportMeasurement = "measured";
const workspaceDashboardRequests = [];
const accountPages = [];
const accountLookups = [];
const apiRequests = [];
const logoutRequests = [];
const preferenceWrites = [];
const previewRequests = [];
const creativePreviewRequests = [];
const audienceRequests = [];
const productPolicy = {
  excluded: false,
  promotion_surfaces: null,
  ad_formats: null,
  ad_image_url: null,
  link_rel: null,
};
function productLinkRelations() {
  return {
    configurable_link_rel: account.configurable_link_rel,
    link_rel_options: account.link_rel_options,
    account_link_rel: account.configurable_link_rel ? account.link_rel : ["nofollow"],
    effective_link_rel: account.configurable_link_rel
      ? (productPolicy.link_rel ?? account.link_rel)
      : ["nofollow"],
  };
}
const productPolicyWrites = [];
const websiteImage = creativeProduct(previewProduct).image_url;
const customImage = "https://assets.partner.example/custom-hero.svg";
const managedCustomImage = "https://assets.partner.example/managed-custom-hero.svg";
const uploadedImage = "https://assets.partner.example/partner-ad-images/800.webp?v=upload-1";
const productImageUploads = [];
let unavailableSession = null;
function productAdImage() {
  return {
    default_image_url: websiteImage,
    image_url:
      productPolicy.ad_image_url === uploadedImage
        ? uploadedImage
        : productPolicy.ad_image_url
          ? managedCustomImage
          : websiteImage,
    ad_image_status: productPolicy.ad_image_url ? "ready" : "default",
  };
}
const connectionFixture = createConnectionFixture({ account, origin: () => origin });
const mock = createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  const path = url.pathname;
  apiRequests.push({ path, method: request.method });
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
  if (path.endsWith("/auth/sso/logout")) {
    logoutRequests.push({ method: request.method, mode });
    assert.equal(request.headers["x-csrf-token"], "test-csrf");
    response.writeHead(200, {
      "Content-Type": "application/json",
      "Set-Cookie": "devfeed_partner_session=; Max-Age=0; Path=/",
    });
    response.end(JSON.stringify({ url: "https://identity.example/end-session?state=partner" }));
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
  if (path === unavailableSession?.path) {
    response.writeHead(503);
    response.end(JSON.stringify({ detail: unavailableSession.detail }));
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
  if (await manualProducts.handle(request, response, url, memberAccounts)) return;
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
        ["outbound_destination", "audience", "ad_formats", "link_rel"].includes(key),
      ),
    );
    if (payload.outbound_destination !== undefined)
      assert.ok(["product_url", "listing_url"].includes(payload.outbound_destination));
    preferenceWrites.push(payload);
    if (payload.outbound_destination !== undefined)
      account.outbound_destination = payload.outbound_destination;
    if (payload.audience !== undefined) account.audience = payload.audience;
    if (payload.ad_formats !== undefined) account.ad_formats = payload.ad_formats;
    if (payload.link_rel !== undefined) account.link_rel = payload.link_rel;
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
              product_url: "https://api-checker.example/",
              tagline: creativeProduct(previewProduct).tagline,
              pricing: "freemium",
              assessment: { state: "qualified", reason: productReviewReason },
              revision: 1,
              source: "imported",
              manual_active: null,
              editable: false,
              active: true,
              technologies: ["Kubernetes"],
              status: "approved",
              commercial_approved: true,
              approval_source: "qualified",
              globally_excluded: false,
              ...productPolicy,
              ...productLinkRelations(),
            },
          ]
        : [];
    items.push(
      ...manualProducts.products.filter(
        (item) =>
          (item.active || url.searchParams.get("include_archived") === "true") &&
          memberAccounts.some((member) => member.id === item.account_id) &&
          (!selectedId || item.account_id === selectedId) &&
          (!productId || item.id === productId) &&
          item.name.toLowerCase().includes(query),
      ),
    );
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
  if (/^\/v1\/partner\/accounts\/[a-f0-9-]+\/products\/[a-f0-9-]+$/i.test(path)) {
    const ownerId = path.split("/")[4];
    const owner = memberAccounts.find((item) => item.id === ownerId);
    if (request.method !== "GET" || !owner || !path.endsWith(`/${previewProduct.id}`)) {
      response.writeHead(404);
      response.end("{}");
      return;
    }
    response.end(
      JSON.stringify({
        id: previewProduct.id,
        account_id: owner.id,
        account_name: owner.name,
        name: previewProduct.name,
        product_url: "https://api-checker.example/",
        tagline: creativeProduct(previewProduct).tagline,
        pricing: "freemium",
        assessment: { state: "qualified", reason: productReviewReason },
        revision: 1,
        source: "imported",
        manual_active: null,
        editable: false,
        active: true,
        technologies: ["Kubernetes"],
        status: "approved",
        commercial_approved: true,
        approval_source: "qualified",
        globally_excluded: false,
        ...productPolicy,
        ...productLinkRelations(),
      }),
    );
    return;
  }
  if (path.endsWith(`/products/${previewProduct.id}/ad-image`)) {
    assert.equal(path, `/v1/partner/accounts/${account.id}/products/${previewProduct.id}/ad-image`);
    assert.equal(request.method, "POST");
    assert.equal(request.headers["x-csrf-token"], "test-csrf");
    assert.match(request.headers["content-type"], /^multipart\/form-data; boundary=/);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const form = await new Response(Buffer.concat(chunks), {
      headers: { "content-type": request.headers["content-type"] },
    }).formData();
    const file = form.get("file");
    assert.equal(file.name, "campaign.png");
    assert.equal(file.type, "image/png");
    assert.ok(file.size > 0);
    productImageUploads.push({
      account_id: account.id,
      product_id: previewProduct.id,
      name: file.name,
    });
    productPolicy.ad_image_url = uploadedImage;
    response.end(
      JSON.stringify({
        account_id: account.id,
        product_id: previewProduct.id,
        ...productPolicy,
        ...productAdImage(),
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
          ["excluded", "promotion_surfaces", "ad_formats", "ad_image_url", "link_rel"].includes(
            key,
          ),
        ),
      );
      productPolicyWrites.push(payload);
      Object.assign(productPolicy, payload);
    }
    response.end(
      JSON.stringify({
        account_id: account.id,
        product_id: previewProduct.id,
        ...productPolicy,
        ...productAdImage(),
        ...productLinkRelations(),
      }),
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
        product:
          isBeta || productPolicy.excluded
            ? null
            : { ...creativeProduct(previewProduct), ...productAdImage() },
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
    const impressions =
      reportMeasurement === "unmeasured"
        ? 0
        : scope.reduce(
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
    const clicks =
      reportMeasurement !== "measured"
        ? 0
        : scope.reduce(
            (total, item) =>
              total +
              (mode === "many" ? 10 : item.id === account.id ? (days === "7" ? 35 : 120) : 6),
            0,
          );
    const measured = scope.length > 0 && reportMeasurement !== "unmeasured";
    const formatTotals = [
      {
        ad_format: "feed",
        impressions: Math.floor(impressions * 0.6),
        clicks: Math.floor(clicks * 0.75),
      },
      {
        ad_format: "header_sponsorship",
        impressions: Math.floor(impressions * 0.3),
        clicks: clicks - Math.floor(clicks * 0.75),
      },
      {
        ad_format: "engagement",
        impressions: impressions - Math.floor(impressions * 0.6) - Math.floor(impressions * 0.3),
        clicks: 0,
      },
    ].map((row) => ({
      ...row,
      ctr: row.impressions ? (100 * row.clicks) / row.impressions : null,
      measured_days: 3,
    }));
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
          measured_days: measured ? 3 : 0,
        },
        last_updated_at: measured ? "2026-10-07T10:00:00Z" : null,
        trend: measured
          ? [
              { day: "2026-10-05", impressions: Math.floor(impressions / 4), clicks: 0 },
              {
                day: "2026-10-06",
                impressions: Math.floor(impressions / 4),
                clicks: Math.floor(clicks / 2),
              },
              {
                day: "2026-10-07",
                impressions: impressions - 2 * Math.floor(impressions / 4),
                clicks: clicks - Math.floor(clicks / 2),
              },
            ]
          : [],
        assets: sortedAssets.slice(offset, offset + limit),
        asset_total: sortedAssets.length,
        format_totals: measured ? formatTotals : [],
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

async function checkConcreteApiRoutes() {
  for (const { path, method, status, allowed } of [
    { path: "/api/v1/admin/accounts", method: "POST", status: 404 },
    { path: "/api/v1/user/auth/me", method: "POST", status: 404 },
    { path: "/api/v1/partner/unavailable-resource", method: "POST", status: 404 },
    {
      path: `/api/v1/partner/accounts/${account.id}/products/${previewProduct.id}/ad-image/extra`,
      method: "POST",
      status: 404,
    },
    { path: "/api/v1/partner/auth/me", method: "POST", status: 405 },
    { path: "/api/v1/partner/connections", method: "PUT", status: 405 },
    {
      path: `/api/v1/partner/accounts/${account.id}/products/${previewProduct.id}/promotion`,
      method: "POST",
      status: 405,
    },
    { path: "/api/v1/partner/auth/me", method: "HEAD", status: 405, allowed: ["GET"] },
    {
      path: "/api/v1/partner/connections",
      method: "OPTIONS",
      status: 204,
      allowed: ["GET", "HEAD", "OPTIONS", "POST"],
    },
  ]) {
    const requestsBefore = apiRequests.length;
    const response = await fetch(origin + path, {
      method,
      headers: {
        Origin: origin,
        Cookie: "devfeed_partner_session=partner",
        "Content-Type": "application/json",
      },
      ...(!["GET", "HEAD", "OPTIONS"].includes(method)
        ? { body: '{"gateway-boundary-fixture":true}' }
        : {}),
      redirect: "manual",
      signal: AbortSignal.timeout(5000),
    });
    assert.equal(response.status, status, `${method} ${path}`);
    if (allowed)
      assert.deepEqual(
        new Set(
          response.headers
            .get("allow")
            .split(",")
            .map((value) => value.trim()),
        ),
        new Set(allowed),
        `${method} ${path} advertises its concrete method surface`,
      );
    await response.arrayBuffer();
    assert.equal(
      apiRequests.length,
      requestsBefore,
      `${method} ${path} must not reach the upstream fixture or its body collectors`,
    );
  }
  console.log(
    "Partner concrete API routes passed: namespace isolation, unknown routes, methods, HEAD, native OPTIONS, and no upstream requests.",
  );
}

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

async function checkDeliveryChart(page, { impressions, clicks }) {
  const trend = page.getByRole("region", { name: "Delivery trend", exact: true });
  const chart = trend.getByRole("figure", { name: "Impressions and clicks", exact: true });
  const legend = trend.getByRole("list", { name: "Chart series", exact: true });
  await legend.getByText("Impressions (left)", { exact: true }).waitFor();
  await legend.getByText("Clicks (right)", { exact: true }).waitFor();
  assert.equal(await trend.getByRole("button").count(), 0, "Both metrics are visible together");
  await chart.locator(".recharts-line-curve").nth(1).waitFor();
  const rendered = await chart.evaluate((figure) => ({
    curves: [...figure.querySelectorAll(".recharts-line-curve")].map((line) => ({
      path: line.getAttribute("d"),
      color: line.getAttribute("stroke"),
      dash: line.getAttribute("stroke-dasharray"),
    })),
    points: [...figure.querySelectorAll(".recharts-line-dots")].map(
      (line) => line.querySelectorAll(".recharts-line-dot").length,
    ),
    axes: [...figure.querySelectorAll(".recharts-yAxis-tick-labels")].map((axis) =>
      [...axis.querySelectorAll(".recharts-cartesian-axis-tick-value")].map((tick) => ({
        value: tick.textContent,
        x: Number(tick.getAttribute("x")),
      })),
    ),
  }));
  assert.equal(rendered.curves.length, 2, "Two delivery series render in the real SVG");
  assert.ok(rendered.curves.every((line) => line.path?.includes("L")));
  assert.notEqual(rendered.curves[0].color, rendered.curves[1].color);
  assert.equal(rendered.curves[0].dash, null);
  assert.ok(rendered.curves[1].dash, "Line patterns distinguish metrics without color");
  assert.deepEqual(rendered.points, [3, 3], "Unknown dates do not become plotted zeroes");
  assert.equal(rendered.axes.length, 2);
  const zeroTicks = rendered.axes.map((axis) => axis.find((tick) => tick.value === "0"));
  assert.ok(zeroTicks[0], "The impressions scale starts at zero");
  assert.ok(zeroTicks[1], "The clicks scale starts at zero");
  assert.ok(zeroTicks[0].x < zeroTicks[1].x, "Each scale has its own side");
  assert.notDeepEqual(
    rendered.axes[0].map((tick) => tick.value),
    rendered.axes[1].map((tick) => tick.value),
    "The count scales are independent",
  );

  const tooltip = chart.getByRole("status");
  const firstImpressions = Math.floor(impressions / 4).toLocaleString("en");
  const assertTooltip = async (day, expectedClicks) => {
    await tooltip.getByText(day, { exact: true }).waitFor();
    await tooltip
      .getByText("Impressions", { exact: true })
      .locator("..")
      .getByText(firstImpressions, { exact: true })
      .waitFor();
    await tooltip
      .getByText("Clicks", { exact: true })
      .locator("..")
      .getByText(expectedClicks.toLocaleString("en"), { exact: true })
      .waitFor();
  };
  // Hover a rendered observation: the same tooltip includes the recorded zero-click count.
  await chart.locator(".recharts-line-dots").first().locator(".recharts-line-dot").first().hover();
  await assertTooltip("Oct 5", 0);
  // Navigate the shared date axis with Recharts' real accessibility layer as well.
  await page.mouse.move(0, 0);
  const application = chart.getByRole("application");
  await application.focus();
  const days = new URL(page.url()).searchParams.get("days") === "7" ? 7 : 30;
  // The same chart retains its keyboard index when the viewport changes.
  for (let index = 0; index < days; index++) await application.press("ArrowLeft");
  for (let index = 0; index < days - 3; index++) await application.press("ArrowRight");
  await assertTooltip("Oct 5", 0);
  await application.press("ArrowRight");
  await assertTooltip("Oct 6", Math.floor(clicks / 2));
  await application.evaluate((element) => element.blur());
  await page.mouse.move(0, 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
}

async function checkFormatChart(page, { impressions, clicks }) {
  const formats = page.getByRole("region", { name: "Format comparison", exact: true });
  const controls = formats.getByRole("group", { name: "Format metric", exact: true });
  const legend = formats.getByRole("list", { name: "Format totals", exact: true });
  const rows = legend.getByRole("listitem");
  const impressionButton = controls.getByRole("button", { name: "Impressions", exact: true });
  const clickButton = controls.getByRole("button", { name: "Clicks", exact: true });
  await impressionButton.click();
  assert.equal(await impressionButton.getAttribute("aria-pressed"), "true");
  assert.equal(await clickButton.getAttribute("aria-pressed"), "false");
  const impressionChart = formats.getByRole("figure", {
    name: "Impression share by format",
    exact: true,
  });
  await impressionChart.locator(".recharts-pie-sector").nth(2).waitFor();
  const sectors = await impressionChart
    .locator(".recharts-sector")
    .evaluateAll((nodes) =>
      nodes.map((node) => ({ path: node.getAttribute("d"), fill: node.getAttribute("fill") })),
    );
  assert.equal(sectors.length, 3, "All three measured formats have visible impression shares");
  assert.equal(new Set(sectors.map((sector) => sector.fill)).size, 3);
  assert.ok(sectors.every((sector) => sector.path && !sector.path.includes("NaN")));
  assert.equal(await rows.count(), 3);
  for (const name of ["Feed", "Header sponsorship", "Engagement"])
    await rows.filter({ hasText: name }).waitFor();
  const feed = rows.filter({ hasText: "Feed" });
  await feed
    .getByText(Math.floor(impressions * 0.6).toLocaleString("en"), { exact: true })
    .waitFor();
  await feed.getByText("60.0%", { exact: false }).waitFor();
  const firstSlice = impressionChart.locator(".recharts-sector").first();
  await firstSlice.scrollIntoViewIfNeeded();
  const hoverPoint = await firstSlice.evaluate((path) => {
    const box = path.getBBox();
    const candidates = [];
    for (let row = 1; row < 10; row++) {
      for (let column = 1; column < 10; column++) {
        const x = box.x + (box.width * column) / 10;
        const y = box.y + (box.height * row) / 10;
        if (
          [
            [0, 0],
            [-2, 0],
            [2, 0],
            [0, -2],
            [0, 2],
          ].every(([dx, dy]) => path.isPointInFill(new DOMPoint(x + dx, y + dy)))
        )
          candidates.push({ x, y });
      }
    }
    candidates.sort(
      (left, right) =>
        Math.hypot(left.x - box.x - box.width / 2, left.y - box.y - box.height / 2) -
        Math.hypot(right.x - box.x - box.width / 2, right.y - box.y - box.height / 2),
    );
    if (!candidates.length) throw new Error("No visible interior point in the format sector");
    const point = new DOMPoint(candidates[0].x, candidates[0].y).matrixTransform(
      path.getScreenCTM(),
    );
    return { x: point.x, y: point.y };
  });
  await page.mouse.move(hoverPoint.x, hoverPoint.y);
  const tooltip = impressionChart.getByRole("status");
  await tooltip.getByText("Feed", { exact: true }).waitFor();
  await tooltip.getByText("60.0%", { exact: true }).waitFor();
  await tooltip
    .getByText(Math.floor(impressions * 0.6).toLocaleString("en"), { exact: true })
    .waitFor();
  const tooltipBox = await tooltip.boundingBox();
  assert.ok(
    tooltipBox.x >= 0 && tooltipBox.x + tooltipBox.width <= page.viewportSize().width,
    "The sector tooltip fits inside the viewport",
  );
  await formats.screenshot({
    path: root + `reports/partner/performance-format-tooltip-${page.viewportSize().width}.png`,
  });
  await page.mouse.move(0, 0);
  await clickButton.click();
  assert.equal(await clickButton.getAttribute("aria-pressed"), "true");
  assert.equal(await impressionButton.getAttribute("aria-pressed"), "false");
  const clickChart = formats.getByRole("figure", { name: "Click share by format", exact: true });
  await clickChart.locator(".recharts-pie-sector").nth(1).waitFor();
  assert.equal(
    await clickChart.locator(".recharts-pie-sector").count(),
    2,
    "Formats without clicks keep their legend row without inventing a donut sector",
  );
  await feed.getByText(Math.floor(clicks * 0.75).toLocaleString("en"), { exact: true }).waitFor();
  const engagement = rows.filter({ hasText: "Engagement" });
  await engagement.getByText("0", { exact: true }).waitFor();
  await engagement.getByText("0.0%", { exact: false }).waitFor();
  await engagement.getByText("0.00% CTR", { exact: false }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await impressionButton.click();
}

async function checkPartnerSidebar(page) {
  const initialUrl = page.url();
  const sidebar = page.locator("#partner-sidebar");
  const navigation = page.getByRole("navigation", { name: "Partner portal", exact: true });
  const key = "devfeed:partner-sidebar-expanded";
  async function width(expected) {
    await page.waitForFunction(
      (value) =>
        Math.abs(document.getElementById("partner-sidebar").getBoundingClientRect().width - value) <
        1,
      expected,
    );
  }
  // The layout effect marks hydration complete before this test mutates React-owned DOM.
  await page.waitForFunction(() =>
    Boolean(
      document.getElementById("partner-sidebar")?.style.getPropertyValue("--sidebar-top-offset"),
    ),
  );
  await width(76);
  const initialBounds = [];
  for (const height of [1100, 600]) {
    await page.setViewportSize({ width: 1440, height });
    const toggle = await page
      .getByRole("button", { name: "Expand sidebar", exact: true })
      .boundingBox();
    const position = await page.evaluate(() => ({ scrollY, viewportHeight: innerHeight }));
    initialBounds.push({ height, top: toggle.y, bottom: toggle.y + toggle.height, ...position });
  }
  console.log("Partner initial sidebar toggle bounds:", JSON.stringify(initialBounds));
  for (const bounds of initialBounds) {
    assert.equal(bounds.scrollY, 0, "Initial sidebar measurement must precede automatic scrolling");
    assert.ok(
      bounds.top >= 0 && bounds.bottom <= bounds.viewportHeight,
      `Sidebar toggle stays visible in a ${bounds.height}px viewport before interaction`,
    );
  }
  const scrollRange = await page.evaluate(() => {
    const natural = document.documentElement.scrollHeight - innerHeight;
    const spacer = document.createElement("div");
    spacer.dataset.sidebarScrollFixture = "true";
    spacer.setAttribute("aria-hidden", "true");
    spacer.style.height = `${innerHeight * 2}px`;
    document.getElementById("main").append(spacer);
    return { natural, controlled: document.documentElement.scrollHeight - innerHeight };
  });
  console.log("Partner sidebar scroll ranges:", JSON.stringify(scrollRange));
  try {
    assert.ok(scrollRange.controlled >= 80, "The scroll fixture can move the header offscreen");
    await page.evaluate(() => window.scrollTo(0, 80));
    await page.waitForFunction(() => {
      const rect = document.getElementById("partner-sidebar").getBoundingClientRect();
      return scrollY > 0 && rect.top <= 0.5 && Math.abs(rect.height - innerHeight) < 1;
    });
    assert.equal(Math.round(await page.evaluate(() => scrollY)), 80);
    const scrolledToggle = await page
      .getByRole("button", { name: "Expand sidebar", exact: true })
      .boundingBox();
    assert.ok(
      scrolledToggle.y >= 0 && scrolledToggle.y + scrolledToggle.height <= 600,
      "Sidebar toggle remains visible after the header scrolls offscreen",
    );
  } finally {
    await page.evaluate(() => {
      window.scrollTo(0, 0);
      document.querySelector('[data-sidebar-scroll-fixture="true"]')?.remove();
    });
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
  assert.equal(await sidebar.getAttribute("data-expanded"), "false");
  const products = navigation.getByRole("link", { name: "Products", exact: true });
  assert.equal(await products.getAttribute("title"), "Products");
  assert.equal(await products.locator("svg").getAttribute("width"), "20");
  assert.equal(
    await products.locator("span").evaluate((element) => getComputedStyle(element).opacity),
    "0",
  );
  await page.screenshot({
    path: root + "reports/partner/sidebar-collapsed-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).focus();
  await page.keyboard.press("Enter");
  await width(232);
  assert.equal(await page.evaluate((name) => localStorage.getItem(name), key), "true");
  await page.screenshot({
    path: root + "reports/partner/sidebar-expanded-desktop.png",
    fullPage: true,
  });
  await page.reload();
  await width(232);
  await navigation.getByRole("link", { name: "Products", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/products");
  await width(232);
  assert.equal(
    await navigation
      .getByRole("link", { name: "Products", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).focus();
  await page.keyboard.press("Space");
  await width(76);
  assert.equal(await page.evaluate((name) => localStorage.getItem(name), key), "false");
  await page.reload();
  await width(76);
  await page.setViewportSize({ width: 390, height: 844 });
  const disclosure = page.getByRole("button", { name: "Navigation", exact: true });
  await disclosure.waitFor({ state: "visible" });
  assert.equal(await sidebar.isVisible(), false);
  await disclosure.click();
  assert.equal(await disclosure.getAttribute("aria-expanded"), "true");
  for (const label of ["Dashboard", "Performance", "Products", "Connections", "Settings"]) {
    const link = navigation.getByRole("link", { name: label, exact: true });
    await link.waitFor({ state: "visible" });
    assert.equal(
      await link.locator("span").evaluate((element) => getComputedStyle(element).opacity),
      "1",
    );
  }
  assert.equal(
    await page.getByRole("button", { name: "Expand sidebar", exact: true }).isVisible(),
    false,
  );
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: root + "reports/partner/sidebar-open-mobile.png", fullPage: true });
  await products.focus();
  await page.keyboard.press("Escape");
  assert.equal(await disclosure.getAttribute("aria-expanded"), "false");
  assert.equal(await disclosure.evaluate((element) => element === document.activeElement), true);
  await disclosure.focus();
  await page.keyboard.press("Space");
  await navigation.getByRole("link", { name: "Performance", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/performance");
  assert.equal(await disclosure.getAttribute("aria-expanded"), "false");
  assert.equal(await sidebar.isVisible(), false);
  assert.equal(await page.evaluate((name) => localStorage.getItem(name), key), "false");
  await page.screenshot({
    path: root + "reports/partner/sidebar-closed-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await width(76);
  await page.goto(initialUrl);
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
}

async function checkDashboardDensity(page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  const trend = await page
    .getByRole("region", { name: "Delivery trend", exact: true })
    .boundingBox();
  const clickThrough = await page
    .getByRole("region", { name: "Click-through trend", exact: true })
    .boundingBox();
  const formats = await page
    .getByRole("region", { name: "Format comparison", exact: true })
    .boundingBox();
  const chart = await page
    .getByRole("figure", { name: "Impressions and clicks", exact: true })
    .boundingBox();
  const overview = await page
    .getByRole("region", { name: "Ad performance", exact: true })
    .boundingBox();
  assert.ok(
    chart.height <= 192,
    "The dashboard chart leaves room for the format breakdown and actions",
  );
  assert.ok(
    clickThrough.x >= trend.x + trend.width &&
      formats.x >= clickThrough.x + clickThrough.width &&
      Math.abs(trend.y - formats.y) < 1 &&
      Math.abs(trend.y - clickThrough.y) < 1,
    "Delivery, click-through rate and format comparison share the desktop row",
  );
  assert.ok(
    overview.y + overview.height <= 760,
    "Core dashboard performance fits a laptop viewport",
  );
  const period = await page.getByRole("combobox", { name: "Reporting period" }).boundingBox();
  const heading = await page
    .getByRole("heading", { name: "Dashboard", exact: true, level: 1 })
    .boundingBox();
  assert.ok(Math.abs(period.y - heading.y) < 40, "Reporting controls share the page heading row");
  await checkDeliveryChart(page, { impressions: 2400, clicks: 120 });
  await checkFormatChart(page, { impressions: 2400, clicks: 120 });
  await page.screenshot({ path: root + "reports/partner/dashboard-laptop.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await checkFormatChart(page, { impressions: 2400, clicks: 120 });
  const boundaryCards = await Promise.all(
    ["Delivery trend", "Click-through trend", "Format comparison"].map((name) =>
      page.getByRole("region", { name, exact: true }).boundingBox(),
    ),
  );
  assert.ok(
    boundaryCards[1].x >= boundaryCards[0].x + boundaryCards[0].width &&
      boundaryCards[2].x >= boundaryCards[1].x + boundaryCards[1].width,
    "The three charts fit at the desktop breakpoint",
  );
  await page.screenshot({ path: root + "reports/partner/dashboard-1280.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= window.innerWidth);
  const metrics = await Promise.all(
    ["Impressions", "Clicks", "Click-through rate"].map((name) =>
      page.getByRole("region", { name, exact: true }).boundingBox(),
    ),
  );
  assert.ok(
    metrics.every((box) => Math.abs(box.y - metrics[0].y) < 1),
    "Mobile totals remain visible together",
  );
  await checkDeliveryChart(page, { impressions: 2400, clicks: 120 });
  await checkFormatChart(page, { impressions: 2400, clicks: 120 });
  const mobileCards = await Promise.all(
    ["Delivery trend", "Click-through trend", "Format comparison"].map((name) =>
      page.getByRole("region", { name, exact: true }).boundingBox(),
    ),
  );
  assert.ok(
    mobileCards[1].y >= mobileCards[0].y + mobileCards[0].height &&
      mobileCards[2].y >= mobileCards[1].y + mobileCards[1].height,
    "All three chart cards stack in order on mobile",
  );
  await page.screenshot({ path: root + "reports/partner/dashboard-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
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
  await checkConcreteApiRoutes();
  await checkUploadDeadline();
  browser = await chromium.launch({ headless: true });
  await mkdir(root + "reports/partner", { recursive: true });
  await assertPartnerRecoveryJourneys({
    browser,
    origin,
    root,
    productName: previewProduct.name,
    unavailable: (path, detail) => {
      unavailableSession = path ? { path, detail } : null;
    },
    requestCount: (path) => apiRequests.filter((request) => request.path === path).length,
  });
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
  await checkAppIcons(page);
  await mkdir(root + "reports/partner", { recursive: true });
  await page.screenshot({ path: root + "reports/partner/login-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/login-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("link", { name: "Continue to sign in" }).click();
  await page.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  await checkPartnerSidebar(page);
  await assertPartnerThemeJourneys({ browser, context, origin, root });
  await page.getByRole("figure", { name: "Impressions and clicks", exact: true }).waitFor();
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
  await page.getByRole("figure", { name: "Impressions and clicks", exact: true }).waitFor();
  await checkDashboardDensity(page);
  await page.screenshot({ path: root + "reports/partner/dashboard-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: root + "reports/partner/dashboard-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1100 });
  const previewsBeforeSettings = previewRequests.length;
  const creativesBeforeSettings = creativePreviewRequests.length;
  await page.goto(origin + `/settings/${account.id}`);
  const settingsNavigation = page.getByRole("navigation", {
    name: "Account settings sections",
    exact: true,
  });
  await settingsNavigation.getByRole("link", { name: "General", exact: true }).waitFor();
  assert.equal(
    await settingsNavigation
      .getByRole("link", { name: "General", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  await page.getByText("Sponsored · inherited from tier", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: /^Save/ }).count(), 0);
  assert.equal(
    previewRequests.length,
    previewsBeforeSettings,
    "General does not load destination previews",
  );
  assert.equal(
    creativePreviewRequests.length,
    creativesBeforeSettings,
    "General does not load creative previews",
  );
  await page.screenshot({
    path: root + "reports/partner/settings-general-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/settings-general-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await settingsNavigation.getByRole("link", { name: "Click destination", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}/destination`);
  await page.goBack();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}`);
  await settingsNavigation.getByRole("link", { name: "General", exact: true }).waitFor();
  await page.goForward();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}/destination`);
  const settings = page.getByRole("region", { name: "Click destination", exact: true });
  await settings.waitFor();
  assert.equal(
    await settingsNavigation
      .getByRole("link", { name: "Click destination", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  assert.equal(
    await page.getByRole("form", { name: "Ad format settings", exact: true }).count(),
    0,
  );
  assert.equal(await page.getByRole("form", { name: "Audience settings", exact: true }).count(), 0);
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
    previewBox.y > selectorBox.y,
    "Destination preview stays with its controls in the compact settings panel",
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
  await settingsNavigation.getByRole("link", { name: "Ad formats", exact: true }).click();
  await page.waitForURL((url) => url.pathname.endsWith("/formats"));
  assert.equal(await page.getByRole("combobox", { name: "Product click destination" }).count(), 0);
  await settingsNavigation.getByRole("link", { name: "Click destination", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}/destination`);
  await page.reload();
  await page.getByRole("combobox", { name: "Product click destination" }).waitFor();
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
  await settingsNavigation.getByRole("link", { name: "Ad formats", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}/formats`);
  const formatSettings = page.getByRole("form", { name: "Ad format settings", exact: true });
  assert.equal(await formatSettings.getByRole("checkbox", { name: /^Feed/ }).isChecked(), true);
  await formatSettings.getByRole("checkbox", { name: /^Header sponsorship/ }).check();
  await formatSettings.getByRole("checkbox", { name: /^Engagement/ }).check();
  const destinationBeforeFormats = account.outbound_destination;
  const audienceBeforeFormats = structuredClone(account.audience);
  await formatSettings.getByRole("button", { name: "Save ad formats", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Ad formats saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1), {
    ad_formats: ["feed", "header_sponsorship", "engagement"],
  });
  assert.equal(account.outbound_destination, destinationBeforeFormats);
  assert.deepEqual(account.audience, audienceBeforeFormats);
  await page.reload();
  assert.equal(
    await settingsNavigation
      .getByRole("link", { name: "Ad formats", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  assert.equal(
    await formatSettings.getByRole("checkbox", { name: /^Header sponsorship/ }).isChecked(),
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
  await formatSection.getByRole("combobox", { name: "Preview format", exact: true }).click();
  await page.getByRole("option", { name: "Header sponsorship", exact: true }).click();
  await actualCreative.getByRole("article", { name: "Desktop navigation preview" }).waitFor();
  await checkHeaderSponsorshipPreview(page, actualCreative, {
    product: previewProduct,
    toggle: formatSection.locator("summary").filter({ hasText: "Creative preview" }),
  });
  assert.equal(
    await preview.count(),
    0,
    "Destination settings are not mounted on the formats page",
  );
  await page.setViewportSize({ width: 1280, height: 1100 });
  await formatSection.screenshot({ path: root + "reports/partner/ad-formats-desktop.png" });
  await page.screenshot({
    path: root + "reports/partner/settings-formats-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await formatSection.screenshot({ path: root + "reports/partner/ad-formats-mobile.png" });
  await page.screenshot({
    path: root + "reports/partner/settings-formats-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  account.tier = "gold";
  await page.goto(origin + `/performance`);
  await page.getByRole("heading", { name: "Ad performance", exact: true }).waitFor();
  assert.equal(await page.getByRole("combobox", { name: "Partner account" }).count(), 0);
  await page
    .getByRole("region", { name: "Impressions", exact: true })
    .getByText("2,400", { exact: true })
    .waitFor();
  assert.equal(await page.getByRole("figure").count(), 3);
  await page.getByRole("link", { name: "Export daily CSV", exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 7 days" }).click();
  await page.getByText("700", { exact: true }).first().waitFor();
  await page.getByRole("combobox", { name: "Reporting period" }).click();
  await page.getByRole("option", { name: "Last 30 days" }).click();
  await page.getByText("2,400", { exact: true }).first().waitFor();
  await checkDeliveryChart(page, { impressions: 2400, clicks: 120 });
  await checkFormatChart(page, { impressions: 2400, clicks: 120 });
  await mkdir(root + "reports/partner", { recursive: true });
  await page
    .getByRole("region", { name: "Ad performance", exact: true })
    .screenshot({ path: root + "reports/partner/performance-charts-desktop.png" });
  await page.screenshot({ path: root + "reports/partner/desktop.png", fullPage: true });
  const formatChart = page.getByRole("region", { name: "Format comparison", exact: true });
  reportMeasurement = "zero-clicks";
  await page.reload();
  await formatChart.getByRole("button", { name: "Clicks", exact: true }).click();
  await formatChart.getByText("No clicks recorded in this period.", { exact: true }).waitFor();
  assert.equal(await formatChart.getByRole("figure").count(), 0);
  assert.equal(await formatChart.getByRole("listitem").count(), 3);
  assert.equal(await formatChart.getByText("—", { exact: false }).count(), 3);
  await formatChart.screenshot({
    path: root + "reports/partner/performance-format-zero-clicks.png",
  });
  reportMeasurement = "unmeasured";
  await page.reload();
  await formatChart
    .getByText("Format comparisons appear after your first delivery measurements.", { exact: true })
    .waitFor();
  assert.equal(await formatChart.getByRole("figure").count(), 0);
  assert.equal(await formatChart.getByRole("listitem").count(), 0);
  await formatChart.screenshot({
    path: root + "reports/partner/performance-format-unmeasured.png",
  });
  reportMeasurement = "measured";
  await page.reload();
  await checkFormatChart(page, { impressions: 2400, clicks: 120 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Navigation" }).click();
  await page
    .getByRole("navigation", { name: "Partner portal", exact: true })
    .getByRole("link", { name: "Products", exact: true })
    .click();
  await page.waitForURL((url) => url.pathname === `/products`);
  await page.getByRole("table", { name: "Products" }).waitFor();
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
  const productCreative = page.getByRole("region", { name: "Creative preview", exact: true });
  const productPreviewFormat = page.getByRole("combobox", {
    name: "Product preview format",
    exact: true,
  });
  const reviewSummary = page.locator("summary").filter({ hasText: "Review details" });
  const reviewDetails = reviewSummary.locator("..");
  const reviewReason = reviewDetails.getByText(productReviewReason, { exact: true });
  assert.equal(await reviewDetails.evaluate((element) => element.open), false);
  assert.equal(await reviewReason.isVisible(), false);
  const originalDark = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (dark) => document.documentElement.classList.toggle("dark", dark),
      theme === "dark",
    );
    for (const [size, width, height] of [
      ["desktop", 1440, 1000],
      ["mobile", 390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      await page.screenshot({
        path: root + `reports/partner/product-detail-${size}-${theme}.png`,
        fullPage: true,
      });
    }
  }
  await reviewSummary.press("Enter");
  await reviewReason.waitFor({ state: "visible" });
  await page.screenshot({
    path: root + "reports/partner/product-review-expanded-mobile-dark.png",
    fullPage: true,
  });
  await reviewSummary.click();
  assert.equal(await reviewReason.isVisible(), false);
  await page.evaluate(
    (dark) => document.documentElement.classList.toggle("dark", dark),
    originalDark,
  );
  await productPreviewFormat.click();
  await page.getByRole("option", { name: "Engagement", exact: true }).click();
  await productCreative.getByText(previewProduct.name, { exact: true }).waitFor();
  const productRequestsBeforeRefresh = creativePreviewRequests.length;
  const refreshedCreative = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/creative-preview"),
  );
  await page.getByRole("button", { name: "Refresh preview", exact: true }).click();
  await refreshedCreative;
  await productCreative.getByText(previewProduct.name, { exact: true }).waitFor();
  assert.equal(creativePreviewRequests.length, productRequestsBeforeRefresh + 1);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    const selector = await productPreviewFormat.boundingBox();
    const refresh = await page
      .getByRole("button", { name: "Refresh preview", exact: true })
      .boundingBox();
    assert.ok(selector && refresh, "The format and refresh controls stay visible");
    assert.ok(selector.x + selector.width <= refresh.x, "Preview controls share one compact row");
    assert.ok(
      Math.abs(selector.y + selector.height / 2 - (refresh.y + refresh.height / 2)) < 3,
      "The format and refresh controls align vertically",
    );
    await productCreative.locator("..").screenshot({
      path: root + `reports/partner/engagement-preview-${width === 390 ? "mobile" : "desktop"}.png`,
    });
  }
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
  for (const name of ["Feed", "Header sponsorship", "Sponsored content"])
    await page.getByRole("checkbox", { name, exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "Engagement", exact: true }).check();
  await page.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Choose product placements", exact: true }).click();
  await page.getByLabel("Article pages", { exact: true }).uncheck();
  await page.getByLabel("Topic and category pages", { exact: true }).uncheck();
  await page.getByLabel("Search results", { exact: true }).uncheck();
  await page.getByLabel("Header", { exact: true }).uncheck();
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
    ad_image_url: null,
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
    ad_image_url: null,
  });
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  const creative = page.getByRole("region", { name: "Creative preview", exact: true });
  await creative.locator(`img[src="${websiteImage}"]`).waitFor();
  await page.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Custom image", exact: true }).click();
  await page.getByRole("textbox", { name: "Ad image URL", exact: true }).fill(customImage);
  await creative.locator(`img[src="${customImage}"]`).waitFor();
  assert.equal(productPolicy.ad_image_url, null, "A draft image is private until saved");
  await page.screenshot({
    path: root + "reports/partner/product-ad-image-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/product-ad-image-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.deepEqual(productPolicyWrites.at(-1), {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: customImage,
  });
  await page
    .getByRole("region", { name: "Creative preview", exact: true })
    .locator(`img[src="${managedCustomImage}"]`)
    .waitFor();
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByRole("textbox", { name: "Ad image URL", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("textbox", { name: "Ad image URL", exact: true }).inputValue(),
    customImage,
  );
  await page.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Website image", exact: true }).click();
  await creative.locator(`img[src="${websiteImage}"]`).waitFor();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.equal(productPolicy.ad_image_url, customImage, "Cancel retains the saved custom image");
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Website image", exact: true }).click();
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.equal(
    productPolicyWrites.at(-1).ad_image_url,
    null,
    "Saving Website image clears the override explicitly",
  );
  await page
    .getByRole("region", { name: "Creative preview", exact: true })
    .locator(`img[src="${websiteImage}"]`)
    .waitFor();
  await page.setViewportSize({ width: 1280, height: 1100 });
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Exclude this product", exact: true }).click();
  const writesBeforeUpload = productPolicyWrites.length;
  assert.equal(
    await page.getByRole("button", { name: "Upload image", exact: true }).count(),
    0,
    "Website image hides upload controls",
  );
  await page.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Custom image", exact: true }).click();
  await page.getByLabel("Upload ad image file", { exact: true }).setInputFiles({
    name: "campaign.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aIuoAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await page.getByText(/Image uploaded and applied/).waitFor();
  assert.equal(
    new URL(page.url()).pathname,
    `${productPath}/placements`,
    "Uploading stays on the edit page",
  );
  assert.equal(productImageUploads.length, 1);
  assert.equal(
    productPolicyWrites.length,
    writesBeforeUpload,
    "Upload does not submit placement drafts",
  );
  assert.equal(productPolicy.excluded, false, "Upload preserves the saved availability");
  assert.equal(productPolicy.ad_image_url, uploadedImage);
  await page.getByText("Unsaved changes", { exact: true }).waitFor();
  assert.match(
    await page.getByRole("combobox", { name: "Product availability", exact: true }).innerText(),
    /Exclude this product/,
  );
  await creative.locator(`img[src="${uploadedImage}"]`).waitFor();
  await page.screenshot({
    path: root + "reports/partner/product-ad-image-upload-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/product-ad-image-upload-mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.equal(
    productPolicy.ad_image_url,
    uploadedImage,
    "Cancel discards other drafts while retaining the immediately saved upload",
  );
  assert.equal(productPolicy.excluded, false);
  await page.getByRole("link", { name: "Edit placements", exact: true }).click();
  await page.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Website image", exact: true }).click();
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => url.pathname === productPath);
  assert.equal(productPolicy.ad_image_url, null);
  await page.setViewportSize({ width: 1280, height: 1100 });
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
  await assertManualProductJourneys({
    page,
    origin,
    account,
    fixture: manualProducts,
    artifactDir: root + "reports/partner",
  });
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
  await checkDeliveryChart(page, { impressions: 700, clicks: 35 });
  await checkFormatChart(page, { impressions: 700, clicks: 35 });
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
  await settingsNavigation.getByRole("link", { name: "Audience", exact: true }).click();
  await page.waitForURL((url) => url.pathname === `/settings/${account.id}/audience`);
  const audience = page.getByRole("region", { name: "Audience targeting", exact: true });
  assert.equal(
    await page.getByRole("button", { name: "Save click destination", exact: true }).count(),
    0,
  );
  assert.equal(
    await page.getByRole("form", { name: "Ad format settings", exact: true }).count(),
    0,
  );
  await selectAudience(page, audience, audienceRequests);
  await audience.screenshot({
    path: root + "reports/partner/audience-portal-desktop.png",
    animations: "disabled",
  });
  await page.screenshot({
    path: root + "reports/partner/settings-audience-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const audienceTab = await settingsNavigation
    .getByRole("link", { name: "Audience", exact: true })
    .boundingBox();
  const settingsNavBox = await settingsNavigation.boundingBox();
  assert.ok(
    audienceTab.x >= settingsNavBox.x &&
      audienceTab.x + audienceTab.width <= settingsNavBox.x + settingsNavBox.width + 1,
    "The active Audience tab stays visible on mobile without scrolling the page",
  );
  await audience.screenshot({
    path: root + "reports/partner/audience-portal-mobile.png",
    animations: "disabled",
  });
  await page.screenshot({
    path: root + "reports/partner/settings-audience-mobile.png",
    fullPage: true,
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
  await audience.getByRole("button", { name: "Save audience", exact: true }).waitFor();
  await checkAudienceSelection(audience);
  account.tier = "gold";
  account.advanced_targeting = false;
  await page.reload();
  await audience.getByText(/Advanced targeting requires Platinum or Diamond/).waitFor();
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
    .getByRole("table", { name: "Products" })
    .getByText(account.name, { exact: true })
    .waitFor();
  const rejectedInventory = "**/api/v1/partner/inventory?*";
  await page.route(rejectedInventory, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.items = body.items.map((item) => ({
      ...item,
      status: "rejected",
      commercial_approved: false,
    }));
    await route.fulfill({ response, json: body });
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page
    .getByRole("table", { name: "Products" })
    .getByText("Rejected", { exact: true })
    .first()
    .waitFor();
  assert.equal(
    await page
      .getByRole("table", { name: "Products" })
      .getByText("In review", { exact: true })
      .count(),
    0,
  );
  await page.screenshot({ path: root + "reports/partner/rejected-products.png", fullPage: true });
  await page.unroute(rejectedInventory);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  const productAccountFilter = page
    .getByRole("table", { name: "Products" })
    .getByRole("columnheader", { name: /Partner account/ })
    .getByRole("combobox", { name: "Filter products by partner account", exact: true });
  assert.match(await productAccountFilter.innerText(), /All accounts/);
  await page.getByRole("searchbox", { name: "Search products", exact: true }).fill("API");
  await page.getByRole("checkbox", { name: "Show archived products", exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get("q") === "API");
  await productAccountFilter.click();
  await page.getByRole("option", { name: beta.name, exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get("account_id") === beta.id);
  await page.getByText("No products match your filters.", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("q"), "API");
  assert.equal(new URL(page.url()).searchParams.get("include_archived"), "true");
  await productAccountFilter.click();
  await page.getByRole("option", { name: account.name, exact: true }).click();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await page.reload();
  assert.match(await productAccountFilter.innerText(), new RegExp(account.name));
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("q"), "API");
  assert.equal(new URL(page.url()).searchParams.get("include_archived"), "true");
  await page.screenshot({
    path: root + "reports/partner/products-account-filter-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await productAccountFilter.scrollIntoViewIfNeeded();
  await productAccountFilter.click();
  await page.getByRole("option", { name: "All accounts", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/products-account-filter-mobile.png",
    fullPage: true,
  });
  await page.getByRole("option", { name: "All accounts", exact: true }).click();
  await page.waitForURL((url) => !url.searchParams.has("account_id"));
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.get("q"), "API");
  assert.equal(new URL(page.url()).searchParams.get("include_archived"), "true");
  await page.setViewportSize({ width: 1440, height: 1100 });
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
  await settingsNavigation.getByRole("link", { name: "Click destination", exact: true }).click();
  await page
    .getByText("A preview will appear when this account has an accepted product.", { exact: true })
    .waitFor();
  assert.equal(await preview.getByRole("link").count(), 0);
  assert.ok(
    previewRequests.some((request) => request.account === beta.id && request.mode === "superuser"),
  );
  assert.equal(new URL(page.url()).pathname, `/settings/${beta.id}/destination`);
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
    .getByRole("table", { name: "Products" })
    .getByText(bookmarkedAccount.name, { exact: true })
    .waitFor();
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await productAccountFilter.click();
  await page
    .getByPlaceholder("Search partner accounts…", { exact: true })
    .fill(bookmarkedAccount.name);
  await page.getByRole("option", { name: bookmarkedAccount.name, exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get("account_id") === bookmarkedAccount.id);
  await page.reload();
  assert.match(await productAccountFilter.innerText(), new RegExp(bookmarkedAccount.name));
  await page.getByRole("link", { name: previewProduct.name, exact: true }).waitFor();
  await productAccountFilter.click();
  await page.getByRole("option", { name: "All accounts", exact: true }).click();
  await page.waitForURL((url) => !url.searchParams.has("account_id"));
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
  const unauthorizedSection = await context.request.get(
    origin + `/settings/${foreignAccountId}/audience`,
  );
  assert.equal(
    unauthorizedSection.status(),
    404,
    "Nested settings pages retain account authorization",
  );
  const unknownSection = await context.request.get(
    origin + `/settings/${bookmarkedAccount.id}/private`,
  );
  assert.equal(unknownSection.status(), 404, "Unknown settings sections are not routes");
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

  await context.addCookies([{ name: "devfeed_partner_session", value: "partner", url: origin }]);
  account.tier = "platinum";
  account.configurable_link_rel = true;
  await page.goto(origin + `/settings/${account.id}/destination`);
  await page.getByRole("checkbox", { name: "nofollow", exact: true }).waitFor();
  await page
    .getByText("Paid links always include sponsored or nofollow.", { exact: false })
    .waitFor();
  assert.equal(
    await page.getByRole("checkbox", { name: "nofollow", exact: true }).isChecked(),
    true,
  );
  for (const token of account.link_rel_options)
    assert.equal(await page.getByRole("checkbox", { name: token, exact: true }).count(), 1);
  await page.getByRole("checkbox", { name: "nofollow", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "sponsored", exact: true }).check();
  await page.getByRole("button", { name: "Save click destination", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Click destination saved." }).waitFor();
  assert.deepEqual(preferenceWrites.at(-1).link_rel, ["sponsored"]);
  await page.reload();
  assert.equal(
    await page.getByRole("checkbox", { name: "sponsored", exact: true }).isChecked(),
    true,
  );
  await page.screenshot({
    path: root + "reports/partner/link-rel-account-desktop.png",
    fullPage: true,
  });
  await page.goto(origin + `/products/${previewProduct.id}/placements?account_id=${account.id}`);
  await page.getByText("Account default:").waitFor();
  await page.getByRole("combobox", { name: "Product link relationship", exact: true }).click();
  await page.getByRole("option", { name: "Choose product relationship", exact: true }).click();
  await page.getByRole("checkbox", { name: "sponsored", exact: true }).uncheck();
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.endsWith("/placements"));
  assert.deepEqual(productPolicyWrites.at(-1).link_rel, []);
  await page.goto(origin + `/products/${previewProduct.id}/placements?account_id=${account.id}`);
  await page.getByRole("checkbox", { name: "nofollow", exact: true }).waitFor();
  for (const token of account.link_rel_options)
    assert.equal(await page.getByRole("checkbox", { name: token, exact: true }).isChecked(), false);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/link-rel-product-mobile.png",
    fullPage: true,
  });
  await page.getByRole("combobox", { name: "Product link relationship", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account relationship", exact: true }).click();
  await page.getByRole("button", { name: "Save product placements", exact: true }).click();
  await page.waitForURL((url) => !url.pathname.endsWith("/placements"));
  assert.equal(productPolicyWrites.at(-1).link_rel, null);
  account.tier = "gold";
  account.configurable_link_rel = false;
  await page.goto(origin + `/settings/${account.id}/destination`);
  await page.getByText("nofollow", { exact: true }).waitFor();
  assert.equal(await page.getByRole("checkbox", { name: "sponsored", exact: true }).count(), 0);
  await page.getByRole("button", { name: "Save click destination", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Click destination saved." }).waitFor();
  assert.equal("link_rel" in preferenceWrites.at(-1), false);
  assert.deepEqual(
    account.link_rel,
    ["sponsored"],
    "A downgrade preserves the saved eligible-tier choice",
  );

  await context.addCookies([{ name: "devfeed_partner_session", value: "empty", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "No partner accounts yet" }).waitFor();
  const completedLogouts = logoutRequests.length;
  await page.route("https://identity.example/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Provider logout</p>" }),
  );
  await page.getByRole("button", { name: "User menu: Alex Partner" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL("https://identity.example/end-session?state=partner");
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
    "Compact partner dashboard, responsive forms and charts, full-page product and connection CRUD, secure credential controls, settings, report sorting, all-membership aggregation, paginated account records, clean URLs, mobile layouts, role boundaries, and empty states passed.",
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

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
import {
  adFormats,
  creativeProduct,
  installCreativeMedia,
  sponsoredArticle,
  checkHeaderSponsorshipPreview,
} from "../../../../scripts/testing/native-ad-formats.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const tierBenefits = [
  "Partner portal access",
  "Product and ad performance reporting",
  "Relevant product recommendations",
  "Native feed ads, header sponsorship, sponsored content and engagement ads",
  "Product promotion planning",
  "Priority campaign support",
  "Custom partnership and campaign planning",
];
const account = {
  id: "11111111-aaaa-4bbb-8ccc-111111111111",
  name: "API Checker",
  tier: "gold",
  status: "active",
  benefits: tierBenefits.slice(0, 5),
  organic_enabled: true,
  auto_include_products: true,
  promotion_surfaces: ["article", "topic", "feed"],
  allowed_promotion_kinds: ["product", "ad"],
  effective_promotion_kind: "ad",
  promotion_kind_source: "tier",
  promotion_kind: null,
  approval_mode: "qualified",
  outbound_destination: "product_url",
  link_rel: ["nofollow"],
  configurable_link_rel: false,
  link_rel_options: ["nofollow", "sponsored", "ugc", "external"],
  advanced_targeting: false,
  audience: structuredClone(emptyAudience),
  ad_formats: ["feed"],
  allowed_ad_formats: adFormats,
};
const otherAccountId = "99999999-9999-4999-8999-999999999999";
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
  tagline: "Monitor production APIs and diagnose reliability problems.",
  product_url: "https://product.example/start?plan=team&utm_source=devfeed#quickstart",
  listing_url:
    "https://shipyard.example/products/api-checker?ref=catalog&utm_source=devfeed#overview",
};
const previewRequests = [];
const creativePreviewRequests = [];
const sponsorArticleRequests = [];
let assets = [];
const assetRecords = new Map();
const assetRequests = [];
const dashboardRequests = [];
let dashboardUnavailable = false;
const accountRequests = [];
const memberRequests = [];
const campaigns = [];
let productExcluded = false;
const productPolicy = {
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
const websiteImage = creativeProduct(destinationProduct).image_url;
const customImage = "https://assets.partner.example/admin-hero.svg";
const uploadedImage = "https://assets.partner.example/partner-ad-images/800.webp?v=admin-upload-1";
const productImageUploads = [];
function productAdImage() {
  return {
    image_url:
      productPolicy.ad_image_url === uploadedImage
        ? uploadedImage
        : productPolicy.ad_image_url
          ? "https://assets.partner.example/admin-managed-hero.svg"
          : websiteImage,
    default_image_url: websiteImage,
    ad_image_status: productPolicy.ad_image_url ? "ready" : "default",
  };
}
let appearance = { theme: "light" };
const inventoryRequests = [];
const audienceRequests = [];
const inventoryProducts = Array.from({ length: 60 }, (_, index) => ({
  ...catalogProduct,
  id:
    index === 0 ? catalogProduct.id : `66666666-6666-4666-8666-${String(index).padStart(12, "0")}`,
  name: index === 0 ? catalogProduct.name : `Inventory tool ${index + 1}`,
  status: index === 0 ? "pending" : "approved",
  product_url: "https://product.example/",
  tagline: "Monitor and troubleshoot developer applications.",
  pricing: "paid",
  revision: 1,
  source: "imported",
  active: true,
  manual_active: null,
  editable: false,
  assessment: {
    state: index === 0 ? "checking" : "qualified",
    reason: "Checking developer capabilities.",
  },
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
  account.benefits = tierBenefits.slice(
    0,
    ["bronze", "silver", "gold", "platinum", "diamond"].indexOf(account.tier) + 3,
  );
  const paid = ["silver", "gold", "platinum", "diamond"].includes(account.tier);
  account.allowed_promotion_kinds = paid ? ["product", "ad"] : ["product"];
  account.effective_promotion_kind = account.promotion_kind ?? (paid ? "ad" : "product");
  account.promotion_kind_source = account.promotion_kind ? "account" : "tier";
  account.advanced_targeting = ["platinum", "diamond"].includes(account.tier);
  account.configurable_link_rel = account.advanced_targeting;
  account.allowed_ad_formats = paid ? adFormats : ["feed"];
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
    body = ["bronze", "silver", "gold", "platinum", "diamond"].map((tier, index) => ({
      tier,
      benefits: tierBenefits.slice(0, index + 3),
      allowed_promotion_kinds: index >= 1 ? ["product", "ad"] : ["product"],
      default_promotion_kind: index >= 1 ? "ad" : "product",
      advanced_targeting: index >= 3,
      configurable_link_rel: index >= 3,
      link_rel_options: account.link_rel_options,
      allowed_ad_formats: index >= 1 ? adFormats : ["feed"],
    }));
  } else if (path.endsWith("/sponsor-articles")) {
    sponsorArticleRequests.push(
      Object.fromEntries(new URL(req.url, "http://localhost").searchParams),
    );
    body = { items: [sponsoredArticle], total: 1 };
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
  } else if (
    req.method === "GET" &&
    [account.id, otherAccountId].includes(
      path.replace("/v1/admin/partner-accounts/", "").toLowerCase(),
    )
  ) {
    const id = path.split("/").at(-1).toLowerCase();
    accountRequests.push(id);
    body = id === account.id ? account : { ...account, id: otherAccountId, name: "Other partner" };
  } else if (path === `/v1/admin/partner-accounts/${account.id}/destination-preview`) {
    previewRequests.push(path);
    body = { product: destinationProduct };
  } else if (path === `/v1/admin/partner-accounts/${account.id}/creative-preview`) {
    const productId = new URL(req.url, "http://localhost").searchParams.get("product_id");
    if (productId) assert.equal(productId, catalogProduct.id);
    creativePreviewRequests.push({ productId, excluded: productExcluded });
    body = {
      product: productExcluded
        ? null
        : { ...creativeProduct(destinationProduct), ...productAdImage() },
    };
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
      ...(product.id === catalogProduct.id
        ? productPolicy
        : { promotion_surfaces: null, ad_formats: null, ad_image_url: null }),
    }));
    body = { items, total: matching.length };
  } else if (path.endsWith(`/products/${catalogProduct.id}/ad-image`)) {
    assert.equal(req.method, "POST");
    assert.equal(req.headers["x-csrf-token"], "fixture-csrf");
    assert.match(req.headers["content-type"], /^multipart\/form-data; boundary=/);
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const form = await new Response(Buffer.concat(chunks), {
      headers: { "content-type": req.headers["content-type"] },
    }).formData();
    const file = form.get("file");
    assert.equal(file.name, "campaign.png");
    assert.equal(file.type, "image/png");
    assert.ok(file.size > 0);
    productImageUploads.push(file.name);
    productPolicy.ad_image_url = uploadedImage;
    body = {
      account_id: account.id,
      product_id: catalogProduct.id,
      excluded: productExcluded,
      ...productPolicy,
      ...productAdImage(),
    };
  } else if (path.endsWith(`/products/${catalogProduct.id}/promotion`)) {
    if (req.method === "PUT") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      if (payload.excluded !== undefined) productExcluded = payload.excluded;
      if (payload.promotion_surfaces !== undefined)
        productPolicy.promotion_surfaces = payload.promotion_surfaces;
      if (payload.ad_formats !== undefined) productPolicy.ad_formats = payload.ad_formats;
      if (payload.ad_image_url !== undefined) productPolicy.ad_image_url = payload.ad_image_url;
      if (payload.link_rel !== undefined) productPolicy.link_rel = payload.link_rel;
    }
    body = {
      account_id: account.id,
      product_id: catalogProduct.id,
      excluded: productExcluded,
      ...productPolicy,
      ...productAdImage(),
      ...productLinkRelations(),
    };
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
    assetRecords.set(`${account.id}/${body.id}`, body);
  } else if (path.includes("/assets/")) {
    const segments = path.split("/");
    const accountId = segments.at(-3);
    const assetId = segments.at(-1);
    const record = assetRecords.get(`${accountId.toLowerCase()}/${assetId.toLowerCase()}`);
    assetRequests.push({ accountId, assetId, method: req.method, found: !!record });
    if (!record) {
      res.statusCode = 404;
      body = { detail: "Asset not found in this partner account." };
    } else if (req.method === "PUT") {
      let raw = "";
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      writes.push({ path, csrf: req.headers["x-csrf-token"], payload });
      Object.assign(record, payload);
      const report = assets.find((item) => item.id === assetId.toLowerCase());
      if (report) Object.assign(report, payload);
      body = record;
    } else {
      assert.equal(req.method, "GET");
      body = record;
    }
  } else if (path.endsWith("/dashboard")) {
    const params = new URL(req.url, "http://localhost").searchParams;
    const offset = Number(params.get("offset") ?? 0);
    const limit = Number(params.get("limit") ?? 100);
    const days = Number(params.get("days") ?? 30);
    const start = new Date(Date.UTC(2026, 9, 14 - days)).toISOString().slice(0, 10);
    const accountId = path.split("/").at(-2).toLowerCase();
    const scopedAssets = accountId === account.id ? assets : [];
    const items = scopedAssets.slice(offset, offset + limit);
    dashboardRequests.push({ offset, limit, days, ids: items.map((item) => item.id) });
    if (dashboardUnavailable) {
      res.statusCode = 503;
      res.end(JSON.stringify({ detail: "Reporting temporarily unavailable." }));
      return;
    }
    body = {
      account:
        accountId === account.id
          ? account
          : { ...account, id: otherAccountId, name: "Other partner" },
      assets: items,
      asset_total: scopedAssets.length,
      start,
      end: "2026-10-13",
      last_updated_at: "2026-10-13T23:00:00Z",
      totals: { impressions: 3000, clicks: 100, ctr: 100 / 30, measured_days: 3 },
      trend: [
        { day: "2026-10-11", impressions: 1000, clicks: 20 },
        { day: "2026-10-12", impressions: 1200, clicks: 48 },
        { day: "2026-10-13", impressions: 800, clicks: 32 },
      ],
      format_totals: [
        { ad_format: "feed", impressions: 2000, clicks: 40, ctr: 2, measured_days: 3 },
        { ad_format: "engagement", impressions: 1000, clicks: 60, ctr: 6, measured_days: 3 },
      ],
    };
  } else if (path.endsWith("/members")) {
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
    } else {
      memberRequests.push(path);
      body = members;
    }
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
  await installCreativeMedia(context);
  const page = await context.newPage();
  async function accountReport() {
    const report = page.getByRole("region", { name: "Ad performance", exact: true });
    await report
      .getByRole("region", { name: "Impressions", exact: true })
      .getByText("3,000", { exact: true })
      .waitFor();
    await report
      .getByRole("region", { name: "Clicks", exact: true })
      .getByText("100", { exact: true })
      .waitFor();
    // Navigation can replace the previous report between a wait and a separate read.
    const curvesHandle = await page.waitForFunction(() => {
      const nodes = document.querySelectorAll(
        '[aria-label="Ad performance"] [aria-label="Delivery trend"] .recharts-line-curve',
      );
      return nodes.length === 2
        ? [...nodes].map((node) => ({
            stroke: node.getAttribute("stroke"),
            dash: node.getAttribute("stroke-dasharray"),
            path: node.getAttribute("d"),
          }))
        : null;
    });
    const curves = await curvesHandle.jsonValue();
    await curvesHandle.dispose();
    assert.equal(curves.length, 2, "Impressions and clicks are rendered together");
    assert.notEqual(curves[0].stroke, curves[1].stroke);
    assert.ok(curves[1].dash, "Clicks retain their distinct dashed series");
    assert.ok(curves.every((curve) => curve.path && !curve.path.includes("NaN")));
    const formats = report.getByRole("region", { name: "Format comparison", exact: true });
    const controls = formats.getByRole("group", { name: "Format metric", exact: true });
    const impressions = controls.getByRole("button", { name: "Impressions", exact: true });
    const clicks = controls.getByRole("button", { name: "Clicks", exact: true });
    await impressions.click();
    const impressionShare = formats.getByRole("figure", {
      name: "Impression share by format",
      exact: true,
    });
    await impressionShare.locator(".recharts-pie-sector").nth(1).waitFor();
    const legend = formats.getByRole("list", { name: "Format totals", exact: true });
    await legend
      .getByRole("listitem")
      .filter({ hasText: "Feed" })
      .getByText("66.7%", { exact: false })
      .waitFor();
    await clicks.focus();
    await page.keyboard.press("Enter");
    assert.equal(await clicks.getAttribute("aria-pressed"), "true");
    const clickShare = formats.getByRole("figure", { name: "Click share by format", exact: true });
    await clickShare.locator(".recharts-pie-sector").nth(1).waitFor();
    await legend
      .getByRole("listitem")
      .filter({ hasText: "Feed" })
      .getByText("40.0%", { exact: false })
      .waitFor();
    await legend
      .getByRole("listitem")
      .filter({ hasText: "Engagement" })
      .getByText("6.00% CTR", { exact: false })
      .waitFor();
    await impressions.click();
    const chartCards = await Promise.all(
      ["Delivery trend", "Click-through trend", "Format comparison"].map((name) =>
        report.getByRole("region", { name, exact: true }).boundingBox(),
      ),
    );
    if (page.viewportSize().width >= 1280) {
      assert.ok(
        chartCards[1].x >= chartCards[0].x + chartCards[0].width &&
          chartCards[2].x >= chartCards[1].x + chartCards[1].width &&
          Math.abs(chartCards[0].y - chartCards[2].y) < 1,
        "Admin shares the same delivery, CTR and format chart row",
      );
    } else {
      assert.ok(
        chartCards[1].y >= chartCards[0].y + chartCards[0].height &&
          chartCards[2].y >= chartCards[1].y + chartCards[1].height,
        "Admin charts stack in order on mobile",
      );
    }
    return report;
  }
  async function captureAccountPage(view) {
    const output = root + "reports/admin-partner-account";
    await mkdir(output, { recursive: true });
    const beforeViewport = page.viewportSize();
    const beforeTheme = await page.evaluate(() => ({
      theme: document.documentElement.getAttribute("data-theme"),
      dark: document.documentElement.classList.contains("dark"),
      colorScheme: document.documentElement.style.colorScheme,
    }));
    try {
      for (const theme of ["light", "dark"]) {
        for (const width of [1440, 390]) {
          await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
          await page.evaluate((value) => {
            document.documentElement.dataset.theme = value;
            document.documentElement.classList.toggle("dark", value === "dark");
            document.documentElement.style.colorScheme = value;
            window.scrollTo(0, 0);
          }, theme);
          if (view === "overview" || view === "performance") await accountReport();
          assert.equal(
            await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),
            false,
          );
          assert.equal(
            await page.getByRole("dialog").count(),
            0,
            "Account management stays on normal pages",
          );
          const sections = page.getByRole("navigation", { name: "Account sections", exact: true });
          const links = await sections.getByRole("link").evaluateAll((nodes) =>
            nodes.map((node) => ({
              label: node.textContent,
              rect: node.getBoundingClientRect().toJSON(),
            })),
          );
          assert.equal(links.length, 6);
          assert.ok(
            links.every((link) => link.rect.x >= 0 && link.rect.right <= width + 1),
            "All six account sections remain visible without horizontal scrolling",
          );
          if (view.startsWith("settings-")) {
            const settings = sections.getByRole("link", { name: "Settings", exact: true });
            assert.equal(await settings.getAttribute("aria-current"), "page");
            assert.equal(await settings.isVisible(), true);
          }
          if (view === "overview") {
            assert.equal(
              await page.getByRole("heading", { name: "Record information", exact: true }).count(),
              0,
            );
            const layout = await page.getByRole("main").evaluate((main) => {
              const section = (title) =>
                [...main.querySelectorAll("h2")]
                  .find((heading) => heading.textContent === title)
                  .closest("section");
              return {
                delivery: section("Delivery defaults").getBoundingClientRect().toJSON(),
                formats: section("Formats & placements").getBoundingClientRect().toJSON(),
                height: document.documentElement.scrollHeight,
              };
            });
            if (width === 1440) {
              assert.ok(
                layout.formats.x >= layout.delivery.right,
                "Overview uses both desktop columns for useful account policies",
              );
              assert.ok(
                Math.abs(layout.formats.width - layout.delivery.width) <= 2,
                "Overview no longer reserves a narrow UUID sidebar",
              );
              assert.ok(
                layout.height < 1400,
                "Desktop overview stays shorter than the1467px baseline",
              );
            } else {
              assert.ok(
                layout.formats.y >= layout.delivery.bottom - 1,
                "Policy summaries stack on mobile",
              );
              assert.ok(
                layout.height < 2500,
                `Mobile overview stays bounded with all three charts stacked (${layout.height}px)`,
              );
            }
          }
          await page.screenshot({
            path: `${output}/${view}-${theme}-${width}.png`,
            fullPage: true,
          });
          await page.screenshot({ path: `${output}/${view}-${theme}-${width}-viewport.png` });
        }
      }
    } finally {
      await page.setViewportSize(beforeViewport);
      await page.evaluate((saved) => {
        const root = document.documentElement;
        if (saved.theme === null) root.removeAttribute("data-theme");
        else root.setAttribute("data-theme", saved.theme);
        root.classList.toggle("dark", saved.dark);
        root.style.colorScheme = saved.colorScheme;
      }, beforeTheme);
    }
  }
  async function showTierBenefits() {
    await settingsTab("General");
    const details = page.getByLabel("Selected tier benefits", { exact: true });
    if (!(await details.evaluate((node) => node.open))) await details.locator("summary").click();
  }
  async function settingsTab(name) {
    const writesBefore = writes.length;
    const tab = page.getByRole("tab", { name, exact: true });
    await tab.click();
    assert.equal(await tab.getAttribute("aria-selected"), "true");
    assert.equal(
      writes.length,
      writesBefore,
      "Switching settings tabs retains the draft without saving",
    );
  }
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await context.addCookies([{ name: "devfeed_partner_session", value: "partner", url: origin }]);
  await page.goto(origin + "/partnerships/accounts");
  await page.waitForURL("**/login");
  await context.addCookies([{ name: "devfeed_admin_session", value: "admin", url: origin }]);
  for (const section of [
    "unknown",
    "related",
    "edit/extra",
    "campaigns/new/extra",
    "campaigns/campaign%2Fextra/edit",
    `assets/${catalogProduct.id}/edit/extra`,
  ]) {
    const response = await page.goto(origin + `/partnerships/accounts/${account.id}/${section}`);
    // Next may have streamed the authenticated shell before notFound() runs.
    assert.ok([200, 404].includes(response.status()), `Unrecognized account route ${section}`);
    await page.getByRole("heading", { name: "404", exact: true }).waitFor();
    await page.locator('meta[name="robots"][content*="noindex"]').first().waitFor({
      state: "attached",
    });
    assert.equal(await page.getByRole("form").count(), 0);
    assert.equal(await page.getByLabel(/Campaign name/).count(), 0);
  }
  await page.goto(origin + "/partnerships/accounts");
  await page.getByRole("heading", { name: "Partner accounts", exact: true }).waitFor();
  await page.getByRole("link", { name: "API Checker", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await accountReport();
  const accountSections = page.getByRole("navigation", { name: "Account sections", exact: true });
  assert.deepEqual(await accountSections.getByRole("link").allTextContents(), [
    "Overview",
    "Performance",
    "Products",
    "Campaigns",
    "Members",
    "Settings",
  ]);
  assert.equal(
    await accountSections
      .getByRole("link", { name: "Overview", exact: true })
      .getAttribute("aria-current"),
    "page",
  );
  assert.equal(
    dashboardRequests.at(-1).limit,
    1,
    "Overview fetches only one reporting record while using server totals",
  );
  assert.equal(memberRequests.length, 0, "Overview does not fetch memberships");
  assert.ok(
    accountRequests.includes(account.id),
    "Account identity is fetched independently of reports",
  );
  await captureAccountPage("overview");
  await accountSections.getByRole("link", { name: "Performance", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/performance`);
  await accountReport();
  await page.getByRole("link", { name: "Export daily CSV", exact: true }).waitFor();
  assert.equal(await page.getByRole("table", { name: "Account members", exact: true }).count(), 0);
  assert.equal(dashboardRequests.at(-1).limit, 100);
  for (const days of [7, 90, 30]) {
    await page.getByRole("combobox", { name: "Reporting period", exact: true }).click();
    await page.getByRole("option", { name: `Last ${days} days`, exact: true }).click();
    await page
      .getByRole("region", { name: "Ad performance", exact: true })
      .getByText(`3 of ${days} days have measurements.`, { exact: false })
      .waitFor();
    assert.equal(
      dashboardRequests.at(-1).days,
      days,
      "Reporting periods are requested from the server",
    );
  }
  await captureAccountPage("performance");
  dashboardUnavailable = true;
  await page.reload();
  const main = page.getByRole("main");
  await main.getByRole("alert").waitFor();
  assert.equal(await main.getByRole("alert").count(), 1, "A failed report has one error state");
  assert.equal(await main.getByRole("button", { name: "Retry", exact: true }).count(), 1);
  await main
    .getByRole("navigation", { name: "Account sections", exact: true })
    .getByRole("link", { name: "Settings", exact: true })
    .waitFor();
  assert.equal(
    await main.getByRole("table", { name: "Placement reporting records", exact: true }).count(),
    0,
    "A reporting outage cannot masquerade as an empty inventory",
  );
  assert.equal(await main.getByText(/No placement reporting records yet|0 records/i).count(), 0);
  await page.screenshot({
    path: root + "reports/admin-partner-account/performance-error.png",
    fullPage: true,
  });
  dashboardUnavailable = false;
  await main.getByRole("button", { name: "Retry", exact: true }).click();
  await accountReport();
  await main.getByRole("table", { name: "Placement reporting records", exact: true }).waitFor();
  assert.equal(await main.getByRole("alert").count(), 0);
  const reportsBeforeMembers = dashboardRequests.length;
  await page.getByRole("link", { name: "Members", exact: true }).click();
  await page.getByRole("table", { name: "Account members", exact: true }).waitFor();
  assert.equal(
    dashboardRequests.length,
    reportsBeforeMembers,
    "Members load independently of delivery reports",
  );
  assert.equal(
    await page.getByRole("table", { name: "Placement reporting records", exact: true }).count(),
    0,
  );
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
  await settingsTab("Delivery");
  assert.equal(await page.getByLabel("Product exposure limit", { exact: false }).count(), 0);
  assert.equal(await page.locator('[name="frequency_cap"]').count(), 0);
  await page
    .getByRole("region", { name: "Click destination preview", exact: true })
    .getByText("A preview will appear when this account has an accepted product.", { exact: true })
    .waitFor();
  assert.equal(previewRequests.length, 0, "new accounts do not fetch another account's product");
  await settingsTab("General");
  await page
    .getByLabel(/Partner name/)
    .first()
    .fill("New partner");
  await showTierBenefits();
  await page
    .getByLabel("Selected tier benefits", { exact: true })
    .getByText("Partner portal access", { exact: true })
    .waitFor();
  await settingsTab("General");
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
  await settingsTab("Ad formats");
  assert.equal(await page.getByRole("checkbox", { name: /^Feed/ }).isChecked(), true);
  await page.getByRole("checkbox", { name: /^Header sponsorship/ }).check();
  await page.getByRole("checkbox", { name: /^Engagement/ }).check();
  await settingsTab("Audience");
  assert.equal(await page.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  await settingsTab("General");
  await page
    .getByLabel("Selected tier benefits", { exact: true })
    .getByText("Native feed ads, header sponsorship, sponsored content and engagement ads", {
      exact: true,
    })
    .waitFor();
  await settingsTab("Delivery");
  await page.getByRole("combobox", { name: "Default promotion", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Inherit tier (Sponsored)",
    "Product recommendation",
    "Sponsored",
  ]);
  await page.getByRole("option", { name: "Inherit tier (Sponsored)", exact: true }).click();
  await page.getByRole("combobox", { name: "Product acceptance", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Automatic capability checks",
    "Trust account products",
  ]);
  await page.getByRole("option", { name: "Automatic capability checks", exact: true }).click();
  await settingsTab("General");
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
  assert.deepEqual(writes.at(-1).payload.ad_formats, ["feed", "header_sponsorship", "engagement"]);
  // Older cached account responses must not restore or normalize a removed setting.
  account.frequency_cap = 20;
  await accountReport();
  const generalRequests = {
    destination: previewRequests.length,
    creative: creativePreviewRequests.length,
    audience: audienceRequests.length,
    report: dashboardRequests.length,
    members: memberRequests.length,
  };
  await page.goto(origin + `/partnerships/accounts/${account.id}/edit`);
  await page.getByRole("tab", { name: "General", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("tab", { name: "General", exact: true }).getAttribute("aria-selected"),
    "true",
  );
  await captureAccountPage("settings-general");
  await page.waitForTimeout(850);
  assert.deepEqual(
    {
      destination: previewRequests.length,
      creative: creativePreviewRequests.length,
      audience: audienceRequests.length,
      report: dashboardRequests.length,
      members: memberRequests.length,
    },
    generalRequests,
    "General does not preload hidden previews, audience topics, reports or memberships",
  );
  await page
    .getByRole("navigation", { name: "Account sections", exact: true })
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await accountReport();
  const reportsBeforeSettings = dashboardRequests.length;
  const membershipsBeforeSettings = memberRequests.length;
  await page.getByRole("link", { name: "Edit ad formats", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/edit?tab=formats`);
  assert.equal(
    await page.getByRole("tab", { name: "Ad formats", exact: true }).getAttribute("aria-selected"),
    "true",
  );
  assert.equal(
    dashboardRequests.length,
    reportsBeforeSettings,
    "Settings do not load reporting rows",
  );
  assert.equal(
    memberRequests.length,
    membershipsBeforeSettings,
    "Settings do not fetch account memberships",
  );
  await page.reload();
  await settingsTab("Ad formats");
  assert.equal(await page.getByRole("checkbox", { name: /^Header sponsorship/ }).isChecked(), true);
  assert.equal(await page.getByRole("checkbox", { name: /^Engagement/ }).isChecked(), true);
  await settingsTab("General");
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Diamond", exact: true }).click();
  await settingsTab("Ad formats");
  const formatControls = page.getByRole("region", { name: "Automatic ad formats", exact: true });
  await formatControls.locator("summary").filter({ hasText: "Creative preview" }).click();
  const creativePreview = formatControls.getByRole("region", {
    name: "Creative preview",
    exact: true,
  });
  await creativePreview.getByText(destinationProduct.name, { exact: true }).waitFor();
  await creativePreview.locator("img").waitFor();
  await creativePreview.locator("img").evaluate((img) => img.decode());
  assert.equal(
    await creativePreview.locator("img").evaluate((img) => img.complete && img.naturalWidth > 0),
    true,
  );
  await formatControls.getByRole("combobox", { name: "Preview format", exact: true }).click();
  await page.getByRole("option", { name: "Header sponsorship", exact: true }).click();
  await creativePreview.getByRole("article", { name: "Desktop navigation preview" }).waitFor();
  await creativePreview.getByText("Sponsored", { exact: true }).waitFor();
  assert.equal(await creativePreview.getByText("Visit website", { exact: true }).count(), 0);
  await checkHeaderSponsorshipPreview(page, creativePreview, {
    product: destinationProduct,
    toggle: formatControls.locator("summary").filter({ hasText: "Creative preview" }),
  });
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await formatControls.screenshot({
      path: root + `reports/partner/admin-ad-formats-${width === 390 ? "mobile" : "desktop"}.png`,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
  await captureAccountPage("settings-formats");
  await settingsTab("Audience");
  const audience = page.getByRole("region", { name: "Audience targeting", exact: true });
  await selectAudience(page, audience, audienceRequests);
  await captureAccountPage("settings-audience");
  await settingsTab("Delivery");
  await page.getByText(/Effective: Sponsored · inherited from tier/).waitFor();
  await page.getByRole("combobox", { name: "Default promotion", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Inherit tier (Sponsored)",
    "Product recommendation",
    "Sponsored",
  ]);
  await page.getByRole("option", { name: "Inherit tier (Sponsored)", exact: true }).click();
  await page.getByRole("combobox", { name: "Product acceptance", exact: true }).click();
  await page.getByRole("option", { name: "Trust account products", exact: true }).click();
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
  await captureAccountPage("settings-delivery");
  await settingsTab("General");
  await showTierBenefits();
  await page
    .getByLabel("Selected tier benefits", { exact: true })
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
  await settingsTab("Audience");
  await audience.screenshot({
    path: root + "reports/partner/audience-admin-mobile.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await settingsTab("Audience");
  await audience.screenshot({
    path: root + "reports/partner/audience-admin-desktop.png",
    animations: "disabled",
  });
  await settingsTab("General");
  await page.getByRole("combobox", { name: "Status" }).click();
  await page.getByRole("option", { name: "Paused", exact: true }).click();
  await settingsTab("Delivery");
  await page.getByLabel("Reader feeds", { exact: true }).uncheck();
  await page.getByLabel("Search results", { exact: true }).uncheck();
  await page.getByLabel("Header", { exact: true }).uncheck();
  assert.equal(await page.getByLabel("Product exposure limit", { exact: false }).count(), 0);
  await page.getByRole("button", { name: "Save partnership" }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await page.getByText(/^Diamond$/i).waitFor();
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
  await page.getByRole("heading", { name: "Delivery defaults", exact: true }).waitFor();
  assert.equal(await page.getByText("Product exposure limit", { exact: true }).count(), 0);
  assert.equal(await page.getByText(/per reader installation per UTC day/).count(), 0);
  await page
    .getByRole("navigation", { name: "Account sections" })
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
  await inventory.getByText(/No matching products/).waitFor();
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
  const editProductPlacements = inventory.getByRole("button", {
    name: `Edit placements for ${catalogProduct.name}`,
    exact: true,
  });
  await editProductPlacements.click();
  const productDialog = page.getByRole("dialog", {
    name: `${catalogProduct.name} placements`,
    exact: true,
  });
  await productDialog.getByRole("button", { name: "Preview this product", exact: true }).click();
  await productDialog
    .getByRole("region", { name: "Creative preview" })
    .getByText(destinationProduct.name, { exact: true })
    .waitFor();
  assert.deepEqual(creativePreviewRequests.at(-1), {
    productId: catalogProduct.id,
    excluded: false,
  });
  const productCreative = productDialog.getByRole("region", {
    name: "Creative preview",
    exact: true,
  });
  const productPreviewFormat = productDialog.getByRole("combobox", {
    name: "Product preview format",
    exact: true,
  });
  await productPreviewFormat.click();
  await page.getByRole("option", { name: "Engagement", exact: true }).click();
  await productCreative.getByText(destinationProduct.name, { exact: true }).waitFor();
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await productCreative.locator("..").screenshot({
      path:
        root +
        `reports/partner/admin-engagement-preview-${width === 390 ? "mobile" : "desktop"}.png`,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
  await productPreviewFormat.click();
  await page.getByRole("option", { name: "Feed", exact: true }).click();
  await productDialog.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Exclude this product", exact: true }).click();
  await productDialog.getByText("Unsaved changes", { exact: true }).waitFor();
  await productDialog.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Choose product formats", exact: true }).click();
  for (const name of ["Feed", "Header sponsorship", "Sponsored content"])
    await productDialog.getByRole("checkbox", { name, exact: true }).uncheck();
  await productDialog.getByRole("checkbox", { name: "Engagement", exact: true }).check();
  await productDialog.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Choose product placements", exact: true }).click();
  await productDialog.getByLabel("Article pages", { exact: true }).uncheck();
  await productDialog.getByLabel("Topic and category pages", { exact: true }).uncheck();
  await productDialog.getByLabel("Reader feeds", { exact: true }).check();
  await productDialog.getByLabel("Search results", { exact: true }).uncheck();
  await productDialog.getByLabel("Header", { exact: true }).uncheck();
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.deepEqual(writes.at(-1).payload, {
    excluded: true,
    ad_formats: ["engagement"],
    promotion_surfaces: ["feed"],
    ad_image_url: null,
    link_rel: null,
  });
  await page.reload();
  await editProductPlacements.click();
  await productDialog.getByText(/This product is excluded/).waitFor();
  await productDialog.getByRole("button", { name: "Preview this product", exact: true }).click();
  await productDialog.getByText(/This product is unavailable for preview/).waitFor();
  assert.deepEqual(creativePreviewRequests.at(-1), {
    productId: catalogProduct.id,
    excluded: true,
  });
  await productDialog.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Allow this product", exact: true }).click();
  await productDialog.getByText("Unsaved changes", { exact: true }).waitFor();
  assert.equal(productExcluded, true, "The availability draft is applied only after save");
  assert.equal(
    await productDialog.getByRole("checkbox", { name: "Engagement", exact: true }).isChecked(),
    true,
  );
  assert.equal(
    await productDialog.getByRole("checkbox", { name: "Feed", exact: true }).isChecked(),
    false,
  );
  await productDialog.getByRole("combobox", { name: "Product formats", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account formats", exact: true }).click();
  await productDialog.getByRole("combobox", { name: "Product placements", exact: true }).click();
  await page.getByRole("option", { name: "Inherit account placements", exact: true }).click();
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.deepEqual(writes.at(-1).payload, {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
    link_rel: null,
  });
  await editProductPlacements.click();
  await productDialog.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Custom image", exact: true }).click();
  await productDialog.getByRole("textbox", { name: "Ad image URL", exact: true }).fill(customImage);
  await productDialog.getByRole("button", { name: "Preview this product", exact: true }).click();
  await productDialog
    .getByRole("region", { name: "Creative preview", exact: true })
    .locator(`img[src="${customImage}"]`)
    .waitFor();
  assert.equal(productPolicy.ad_image_url, null, "Admin image draft remains local before save");
  await page.screenshot({
    path: root + "reports/partner/admin-product-ad-image-desktop.png",
    fullPage: true,
  });
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.equal(writes.at(-1).payload.ad_image_url, customImage);
  assert.equal(
    "image_url" in writes.at(-1).payload,
    false,
    "Read-only image metadata is never submitted",
  );
  await editProductPlacements.click();
  await productDialog.getByRole("textbox", { name: "Ad image URL", exact: true }).waitFor();
  assert.equal(
    await productDialog.getByRole("textbox", { name: "Ad image URL", exact: true }).inputValue(),
    customImage,
  );
  await productDialog.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Website image", exact: true }).click();
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.equal(writes.at(-1).payload.ad_image_url, null);
  await editProductPlacements.click();
  await productDialog.getByRole("combobox", { name: "Product availability", exact: true }).click();
  await page.getByRole("option", { name: "Exclude this product", exact: true }).click();
  const writesBeforeUpload = writes.length;
  assert.equal(
    await productDialog.getByRole("button", { name: "Upload image", exact: true }).count(),
    0,
    "Website image hides upload controls",
  );
  await productDialog.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Custom image", exact: true }).click();
  await productDialog.getByLabel("Upload ad image file", { exact: true }).setInputFiles({
    name: "campaign.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aIuoAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await productDialog.getByText(/Image uploaded and applied/).waitFor();
  assert.equal(productImageUploads.length, 1);
  assert.equal(writes.length, writesBeforeUpload);
  assert.equal(productExcluded, false, "Admin upload leaves unrelated drafts unsaved");
  await productDialog.getByText("Unsaved changes", { exact: true }).waitFor();
  await productDialog
    .getByRole("region", { name: "Creative preview", exact: true })
    .locator(`img[src="${uploadedImage}"]`)
    .waitFor();
  await page.setViewportSize({ width: 1440, height: 1800 });
  await productDialog.screenshot({
    path: root + "reports/partner/admin-product-ad-image-upload-desktop.png",
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await productDialog.getByRole("button", { name: "Close placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.equal(
    productPolicy.ad_image_url,
    uploadedImage,
    "Closing retains the immediately applied upload",
  );
  await editProductPlacements.click();
  await productDialog.getByRole("combobox", { name: "Ad image", exact: true }).click();
  await page.getByRole("option", { name: "Website image", exact: true }).click();
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.equal(productPolicy.ad_image_url, null);
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
  assert.equal(writes.at(-1).payload.ad_format, "feed");
  assert.equal(writes.at(-1).payload.article_id, null);
  assert.equal(
    writes.at(-1).payload.audience,
    null,
    "Dated campaigns inherit account audience by default",
  );
  assert.deepEqual(writes.at(-1).payload.surfaces, ["article", "topic"]);
  await page.getByRole("link", { name: "October catalog promotion", exact: true }).click();
  await page.waitForURL(`**/campaigns/${campaigns[0].id}/edit`);
  await page.reload();
  assert.equal(await page.getByLabel(/Campaign name/).inputValue(), campaigns[0].name);
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
  await settingsTab("Delivery");
  await page.getByLabel("Reader feeds", { exact: true }).check();
  await page.getByRole("button", { name: "Save partnership" }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  await page.getByRole("link", { name: "Campaigns", exact: true }).click();
  await page.getByRole("link", { name: "Create paid campaign", exact: true }).click();
  await page.getByLabel(/Campaign name/).fill("API reliability sponsored article");
  await page.getByLabel("Starts", { exact: false }).fill("2026-10-01T00:00");
  await page.getByLabel("Ends", { exact: false }).fill("2026-11-01T00:00");
  await page.getByRole("combobox", { name: "Ad format", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), [
    "Feed",
    "Header sponsorship",
    "Sponsored content",
    "Engagement",
  ]);
  await page.getByRole("option", { name: "Sponsored content", exact: true }).click();
  await page.getByRole("combobox", { name: "Sponsored article", exact: true }).click();
  await page.getByRole("option", { name: sponsoredArticle.title }).click();
  await page.locator("summary").filter({ hasText: "Creative preview" }).click();
  const articleCreative = page.getByRole("region", { name: "Creative preview", exact: true });
  await articleCreative.getByText(`/articles/${sponsoredArticle.slug}`, { exact: true }).waitFor();
  assert.equal(
    await articleCreative.getByText(sponsoredArticle.canonical_url, { exact: true }).count(),
    0,
  );
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await articleCreative.screenshot({
      path:
        root +
        `reports/partner/sponsored-content-preview-${width === 390 ? "mobile" : "desktop"}.png`,
    });
  }
  await page.getByRole("button", { name: "Create campaign", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/campaigns`);
  assert.equal(writes.at(-1).payload.ad_format, "sponsored_content");
  assert.equal(writes.at(-1).payload.article_id, sponsoredArticle.id);
  assert.deepEqual(writes.at(-1).payload.surfaces, ["feed"]);
  await page.getByRole("link", { name: "API reliability sponsored article", exact: true }).click();
  await page.waitForURL(`**/campaigns/${campaigns[2].id}/edit`);
  const articleRequestsBeforeReload = sponsorArticleRequests.length;
  await page.reload();
  assert.equal(await page.getByLabel(/Campaign name/).inputValue(), campaigns[2].name);
  assert.match(
    await page.getByRole("combobox", { name: "Ad format", exact: true }).innerText(),
    /Sponsored content/,
  );
  await page
    .getByRole("combobox", { name: "Sponsored article", exact: true })
    .filter({ hasText: sponsoredArticle.title })
    .waitFor();
  assert.ok(
    sponsorArticleRequests
      .slice(articleRequestsBeforeReload)
      .some((params) => params.article_id === sponsoredArticle.id),
    "reloading resolves the saved article with an exact scoped lookup",
  );
  await page.goto(origin + `/partnerships/accounts/${account.id}/edit?tab=audience`);
  assert.equal(
    await page.getByRole("tab", { name: "Audience", exact: true }).getAttribute("aria-selected"),
    "true",
  );
  await settingsTab("Audience");
  await checkAudienceSelection(
    page.getByRole("region", { name: "Audience targeting", exact: true }),
  );
  await settingsTab("General");
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Platinum", exact: true }).click();
  await settingsTab("Audience");
  assert.equal(await page.getByLabel("Senior", { exact: true }).isChecked(), true);
  await settingsTab("General");
  await page.getByRole("combobox", { name: "Partnership tier" }).click();
  await page.getByRole("option", { name: "Gold", exact: true }).click();
  await settingsTab("Audience");
  assert.equal(await page.getByLabel("Senior", { exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("combobox", { name: "Programming languages", exact: true }).count(),
    0,
  );
  await page.goto(origin + `/partnerships/accounts/${account.id}/campaigns`);
  await page.setViewportSize({ width: 1440, height: 1100 });
  const manualAssetId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const automaticAssetId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const foreignAssetId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const missingAssetId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const manualAsset = {
    id: manualAssetId,
    name: "Deep-linked manual asset",
    kind: "ad",
    status: "active",
    automatic: false,
    product_id: catalogProduct.id,
    ad_format: "feed",
    campaign_id: null,
    article_id: null,
  };
  assetRecords.set(`${account.id}/${manualAssetId}`, manualAsset);
  assetRecords.set(`${account.id}/${automaticAssetId}`, {
    ...manualAsset,
    id: automaticAssetId,
    name: "Automatic attribution record",
    automatic: true,
    campaign_id: campaigns[0].id,
  });
  assetRecords.set(`${otherAccountId}/${foreignAssetId}`, {
    ...manualAsset,
    id: foreignAssetId,
    name: "Other account asset",
  });
  assets = [
    ...Array.from({ length: 110 }, (_, index) => ({
      id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index + 1).padStart(12, "0")}`,
      name: `Another reporting record ${index + 1}`,
      kind: "ad",
      status: "paused",
      automatic: false,
      aggregated: false,
      product_id: catalogProduct.id,
      ad_format: "feed",
      ad_formats: ["feed"],
      impressions: 10,
      clicks: 0,
      ctr: 0,
      measured_days: 1,
    })),
    {
      ...manualAsset,
      aggregated: false,
      ad_formats: ["feed"],
      impressions: 20,
      clicks: 1,
      ctr: 5,
      measured_days: 1,
    },
  ];
  const reportsBeforeBookmark = dashboardRequests.length;
  const assetsBeforeBookmark = assetRequests.length;
  await page.goto(origin + `/partnerships/accounts/${account.id}/assets/${manualAssetId}/edit`);
  await page.getByRole("heading", { name: "Edit Deep-linked manual asset", exact: true }).waitFor();
  assert.equal(await page.getByLabel(/Asset name/).inputValue(), manualAsset.name);
  assert.equal(
    dashboardRequests.length,
    reportsBeforeBookmark,
    "Asset bookmarks resolve without fetching the paginated dashboard",
  );
  assert.equal(await page.getByRole("form", { name: "Asset settings", exact: true }).count(), 1);
  assert.equal(
    await page.getByRole("form", { name: "Partner account settings", exact: true }).count(),
    0,
  );
  assert.ok(
    assetRequests
      .slice(assetsBeforeBookmark)
      .some((request) => request.assetId === manualAssetId && request.found),
    "the bookmarked asset resolves through its account-scoped detail request",
  );
  assert.ok(
    dashboardRequests
      .slice(reportsBeforeBookmark)
      .every((request) => !request.ids.includes(manualAssetId)),
    "the bookmarked asset remains editable outside the first 100 reporting rows",
  );
  await page.reload();
  await page.getByRole("heading", { name: "Edit Deep-linked manual asset", exact: true }).waitFor();
  assert.equal(await page.getByLabel(/Asset name/).inputValue(), manualAsset.name);
  await page.goto(
    origin +
      `/partnerships/accounts/${account.id.toUpperCase()}/assets/${manualAssetId.toUpperCase()}/edit`,
  );
  await page.getByRole("heading", { name: "Edit Deep-linked manual asset", exact: true }).waitFor();
  assert.equal(await page.getByLabel(/Asset name/).inputValue(), manualAsset.name);
  const reportsBeforeOffset = dashboardRequests.length;
  await page.goto(
    origin + `/partnerships/accounts/${account.id}/assets/${manualAssetId}/edit?offset=20&limit=10`,
  );
  await page.getByRole("heading", { name: "Edit Deep-linked manual asset", exact: true }).waitFor();
  assert.equal(await page.getByLabel(/Asset name/).inputValue(), manualAsset.name);
  assert.ok(
    dashboardRequests
      .slice(reportsBeforeOffset)
      .every((request) => !request.ids.includes(manualAssetId)),
    "a different reporting slice cannot select or reject the edit target",
  );
  const writesBeforeAssetSave = writes.length;
  await page.getByLabel(/Asset name/).fill("Saved bookmarked asset");
  await page.getByRole("button", { name: "Save asset", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}/performance`);
  assert.equal(writes.length, writesBeforeAssetSave + 1);
  assert.equal(
    writes.at(-1).path,
    `/v1/admin/partner-accounts/${account.id}/assets/${manualAssetId}`,
  );
  assert.equal(writes.at(-1).payload.name, "Saved bookmarked asset");
  assert.equal(assets[0].name, "Another reporting record 1");
  assert.equal(manualAsset.name, "Saved bookmarked asset");
  for (const [accountId, assetId] of [
    [account.id, missingAssetId],
    [account.id, foreignAssetId],
    [otherAccountId, manualAssetId],
  ]) {
    const unavailableResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/partner-accounts/${accountId}/assets/${assetId}`) &&
        response.request().method() === "GET",
    );
    await page.goto(origin + `/partnerships/accounts/${accountId}/assets/${assetId}/edit`);
    assert.equal((await unavailableResponse).status(), 404);
    await page
      .getByRole("alert")
      .filter({ hasText: /asset.*unavailable/i })
      .waitFor();
    assert.equal(await page.getByRole("form", { name: "Asset settings", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Save asset", exact: true }).count(), 0);
  }
  await page.goto(origin + `/partnerships/accounts/${account.id}/assets/${automaticAssetId}/edit`);
  await page.getByRole("alert").filter({ hasText: "managed automatically" }).waitFor();
  assert.equal(await page.getByRole("form", { name: "Asset settings", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Save asset", exact: true }).count(), 0);
  assert.equal(writes.length, writesBeforeAssetSave + 1);
  assets = [
    {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      name: catalogProduct.name,
      kind: "ad",
      status: "active",
      automatic: false,
      aggregated: true,
      product_id: catalogProduct.id,
      ad_format: "feed",
      ad_formats: ["feed", "header_sponsorship", "engagement"],
      impressions: 100,
      clicks: 5,
      ctr: 5,
      measured_days: 1,
    },
  ];
  await page.goto(origin + `/partnerships/accounts/${account.id}/campaigns`);
  await page.getByRole("link", { name: "Performance", exact: true }).click();
  await accountReport();
  await page.getByText("Product totals", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("Feed · Header sponsorship · Engagement", { exact: true }).count(),
    1,
  );
  assert.equal(
    await page.getByRole("link", { name: `Edit ${catalogProduct.name}`, exact: true }).count(),
    0,
  );
  assert.equal(
    await page
      .getByRole("link", { name: `View policy for ${catalogProduct.name}`, exact: true })
      .getAttribute("href"),
    `/partnerships/accounts/${account.id}/edit`,
  );
  assert.equal(await page.getByRole("link", { name: "Associate asset", exact: true }).count(), 0);
  assert.equal(writes.length, 17);
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
  updatePolicy({ tier: "platinum" });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(origin + `/partnerships/accounts/${account.id}/edit?tab=delivery`);
  await page.getByRole("checkbox", { name: "nofollow", exact: true }).waitFor();
  await page.getByRole("checkbox", { name: "nofollow", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "sponsored", exact: true }).check();
  await page.screenshot({
    path: root + "reports/partner/link-rel-admin-account-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Save partnership", exact: true }).click();
  await page.waitForURL(`**/accounts/${account.id}`);
  assert.deepEqual(writes.at(-1).payload.link_rel, ["sponsored"]);
  await page.goto(origin + `/partnerships/accounts/${account.id}/products`);
  await page
    .getByRole("button", { name: `Edit placements for ${catalogProduct.name}`, exact: true })
    .click();
  await productDialog.getByText("Account default:").waitFor();
  await productDialog
    .getByRole("combobox", { name: "Product link relationship", exact: true })
    .click();
  await page.getByRole("option", { name: "Choose product relationship", exact: true }).click();
  await productDialog.getByRole("checkbox", { name: "sponsored", exact: true }).uncheck();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: root + "reports/partner/link-rel-admin-product-mobile.png",
    fullPage: true,
  });
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.deepEqual(writes.at(-1).payload.link_rel, []);
  await page
    .getByRole("button", { name: `Edit placements for ${catalogProduct.name}`, exact: true })
    .click();
  await productDialog.getByRole("checkbox", { name: "nofollow", exact: true }).waitFor();
  for (const token of account.link_rel_options)
    assert.equal(
      await productDialog.getByRole("checkbox", { name: token, exact: true }).isChecked(),
      false,
    );
  await productDialog
    .getByRole("combobox", { name: "Product link relationship", exact: true })
    .click();
  await page.getByRole("option", { name: "Inherit account relationship", exact: true }).click();
  await productDialog.getByRole("button", { name: "Save product placements", exact: true }).click();
  await productDialog.waitFor({ state: "hidden" });
  assert.equal(writes.at(-1).payload.link_rel, null);
  updatePolicy({ tier: "gold" });
  await page.goto(origin + `/partnerships/accounts/${account.id}/edit?tab=delivery`);
  await page.getByText("nofollow", { exact: true }).waitFor();
  assert.equal(await page.getByRole("checkbox", { name: "sponsored", exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "Admin account route 404s, independent paginated asset deep links and scoped saves, asset ownership/read-only controls, campaign deep links, defaults, automatic inventory search/pagination, readable desktop/mobile Products table, catalog inclusion/exclusion, paid campaign targeting and memberships passed; partner cookie denied.",
  );
} finally {
  await browser?.close();
  next.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

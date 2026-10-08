import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const partnerAccounts = [
  {
    id: "55555555-5555-4555-8555-555555555555",
    name: "Alpha account",
    tier: "bronze",
    status: "active",
    benefits: [],
  },
  {
    id: "66666666-6666-4666-8666-666666666666",
    name: "Beta account",
    tier: "gold",
    status: "active",
    benefits: [],
  },
];
const product = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "API Checker",
  description: "Checks OpenAPI specifications for breaking changes before deployment.",
  product_url: "https://checker.example/",
  pricing: "free",
  technologies: ["OpenAPI"],
  evidence: [
    {
      url: "https://checker.example/docs",
      quote: "Detect breaking changes between OpenAPI specifications.",
      capability: "Check API compatibility in CI.",
    },
  ],
  partnership_type: "launch_platform",
  assessment: { state: "qualified", reason: "Supports specific OpenAPI compatibility checks." },
  excluded: false,
  status: "approved",
  eligible: true,
  revision: 1,
  verified_at: null,
  updated_at: new Date().toISOString(),
  reviews: [],
  metadata_listing_id: "44444444-4444-4444-4444-444444444444",
  listings: [
    {
      id: "44444444-4444-4444-4444-444444444444",
      provider: "nick-launches",
      platform_name: "Nick Launches",
      external_id: "checker",
      name: "API Checker",
      product_url: "https://checker.example/",
      listing_url: "https://nicklaunches.com/products/checker/",
      description: "Check OpenAPI compatibility.",
      pricing: "free",
      attribution: "Via Nick Launches",
      active: true,
      connection_enabled: true,
      identity_status: "resolved",
      identity_reason: null,
      updated_at: "2026-10-02T00:00:00Z",
    },
  ],
};
product.listings.push({
  ...product.listings[0],
  id: "55555555-5555-5555-5555-555555555555",
  provider: "platform-b",
  platform_name: "Platform B",
  attribution: "Via Platform B",
  listing_url: "https://platform-b.example/tools/checker/",
  connection_enabled: false,
  description: "Independent listing of the same developer product.",
});
const articleId = "22222222-2222-2222-2222-222222222222";
const run = {
  id: "33333333-3333-3333-3333-333333333333",
  product_id: product.id,
  created_at: new Date().toISOString(),
  finished_at: new Date().toISOString(),
  status: "succeeded",
  current: true,
  error: null,
  reviews: [],
  snapshot: {
    version: "1",
    product: { ...product },
    articles: [
      {
        id: articleId,
        title: "OpenAPI compatibility in CI",
        text: "Detect breaking OpenAPI changes before merging a pull request.",
        content_type: "tutorial",
        published: true,
        editorial_revision: 0,
      },
    ],
  },
  result: {
    decisions: [
      {
        article_id: articleId,
        relevant: true,
        reason: "This tool checks the API compatibility task discussed in the tutorial.",
        article_quote: "Detect breaking OpenAPI changes before merging a pull request.",
        evidence_index: 0,
        technology: "OpenAPI",
      },
    ],
  },
};
const connection = {
  partnership_type: "launch_platform",
  provider: "nick-launches",
  name: "Nick Launches",
  api_url: "https://nicklaunches.com/api/v1/products/",
  revision: 1,
  sync_interval_minutes: 360,
  enabled: false,
  state: "disconnected",
  last_sync_at: null,
  next_sync_at: null,
  error: null,
  products: 0,
  qualified: 0,
  checking: 0,
  needs_attention: 0,
  excluded: 0,
  ai_enabled: true,
};
let added = false;
const customConnections = [];
let previewValidation = false;
const supported = [
  {
    provider: "nick-launches",
    name: "Nick Launches",
    partnership_type: "launch_platform",
    api_url: connection.api_url,
    description: "Sync developer products from Nick Launches.",
  },
];
const parentId = "66666666-6666-6666-6666-666666666666";
const childId = "77777777-7777-7777-7777-777777777777";
const pipelineJobs = [
  {
    id: parentId,
    provider: "nick-launches",
    operation: "sync",
    parent_id: null,
    external_id: null,
    product_id: null,
    product_name: null,
    status: "succeeded",
    attempts: 1,
    created_at: run.created_at,
    available_at: run.created_at,
    finished_at: run.created_at,
    error: null,
  },
  {
    id: childId,
    provider: "nick-launches",
    operation: "sync_product",
    parent_id: parentId,
    external_id: "checker",
    product_id: product.id,
    product_name: product.name,
    status: "succeeded",
    attempts: 1,
    created_at: run.created_at,
    available_at: run.created_at,
    finished_at: run.created_at,
    error: null,
  },
];
const mutations = [];
const productRequests = [];
const fixture = createServer(async (req, res) => {
  const { pathname: path, searchParams: params } = new URL(req.url, "http://localhost");
  if (path.endsWith("/partner-tools")) productRequests.push(params.toString());
  let body = {};
  if (["POST", "PUT"].includes(req.method)) {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    body = JSON.parse(raw || "{}");
    mutations.push({ path, body, csrf: req.headers["x-csrf-token"] });
  }
  let result;
  if (path.endsWith("/auth/me"))
    result = {
      subject: "fixture",
      name: "Partner reviewer",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (path === "/v1/admin/partner-accounts") {
    const items = partnerAccounts.filter((account) =>
      account.name.toLowerCase().includes((params.get("q") ?? "").toLowerCase()),
    );
    result = { items, total: items.length };
  } else if (path.startsWith("/v1/admin/partner-accounts/") && path.endsWith("/dashboard"))
    result = { account: partnerAccounts.find((account) => path.includes(account.id)) };
  else if (path.endsWith("/settings"))
    result = { appearance: { theme: "dark" }, defaults: { refresh_seconds: 0, overview_days: 30 } };
  else if (path.endsWith("/notifications/config")) result = { enabled: false };
  else if (path.endsWith("/ai/connection"))
    result = { state: "connected", message: "Connected", model: "fixture", quota: [] };
  else if (path.endsWith("/partner-tools/pipeline")) {
    let items = pipelineJobs;
    for (const key of ["status", "operation", "provider", "product_id", "parent_id"]) {
      const value = params.get(key);
      if (value) items = items.filter((job) => job[key] === value);
    }
    if (params.get("q"))
      items = items.filter((job) =>
        `${job.id} ${job.product_name}`.toLowerCase().includes(params.get("q").toLowerCase()),
      );
    result = { items, total: items.length, offset: 0, limit: 25 };
  } else if (path.includes("/partner-tools/pipeline/"))
    result = pipelineJobs.find((job) => path.endsWith(job.id));
  else if (path.endsWith("/partner-tools/evaluations"))
    result = {
      items: [
        {
          id: run.id,
          product_id: product.id,
          product_name: product.name,
          status: run.status,
          attempts: 1,
          created_at: run.created_at,
          available_at: run.created_at,
          finished_at: run.finished_at,
          error: null,
          matches: 1,
          articles: 1,
        },
      ],
      total: 1,
      offset: 0,
      limit: 25,
    };
  else if (path.endsWith(`/partner-tools/evaluations/${run.id}`))
    result = { ...run, attempts: 1, available_at: run.created_at };
  else if (path.includes("/jobs/partner-") && path.endsWith("/logs"))
    result = {
      items: [
        {
          id: "1-0",
          level: "INFO",
          message: path.includes("partner-evaluation")
            ? "Partner evaluation completed"
            : "Partner product sync completed",
          timestamp: new Date().toISOString(),
          fields: { provider: "nick-launches" },
        },
      ],
      next_cursor: "1-0",
      has_more: false,
      job_status: "succeeded",
      truncated: false,
      unreadable_entries: 0,
      max_entries: 1000,
      attempts: 1,
      retention_seconds: 86400,
    };
  else if (path.endsWith("/partner-tools/connector-response"))
    result = {
      data: {
        items: [
          {
            slug: "checker",
            name: "API Checker",
            description: "Check API compatibility before deploying software.",
          },
        ],
        nextCursor: "next-page",
      },
      sampled: true,
    };
  else if (path.endsWith("/partner-tools/connector-preview")) {
    if (previewValidation) {
      previewValidation = false;
      res.writeHead(422, { "content-type": "application/json" });
      res.end(
        JSON.stringify({
          detail: [
            { loc: ["body", "connector", "fields", "name", 0], msg: "Use a valid response path." },
            { loc: ["body", "connector", "max_pages"], msg: "Must be at most 1000." },
          ],
        }),
      );
      return;
    }
    result = {
      discovered: 1,
      products: [
        {
          provider: body.provider,
          external_id: "checker",
          name: "Mapped Checker",
          product_url: "https://checker.example/",
          listing_url: "https://nicklaunches.com/products/checker/",
          description: "Check API compatibility before deployment.",
        },
      ],
      errors: [],
      has_next_page: false,
    };
  } else if (path.endsWith("/partner-tools/providers")) result = supported;
  else if (path.endsWith("/partner-tools/connections")) {
    if (req.method === "POST") {
      if (body.provider !== "nick-launches") {
        const custom = {
          ...connection,
          ...body,
          api_url: body.connector.base_url + body.connector.list_path,
          state: "disconnected",
          products: 0,
          qualified: 0,
          revision: 1,
        };
        customConnections.push(custom);
        res.writeHead(201, { "content-type": "application/json" });
        res.end(JSON.stringify(custom));
        return;
      }
      added = true;
      connection.connector = body.connector;
      connection.name = body.name;
      connection.account_id = body.account_id;
      connection.enabled = body.enabled;
      connection.sync_interval_minutes = body.sync_interval_minutes;
      connection.state = body.enabled ? "idle" : "disconnected";
      result = connection;
    } else result = [...(added ? [connection] : []), ...customConnections];
  } else if (path.endsWith("/partner-tools/connections/nick-launches")) {
    Object.assign(connection, {
      name: body.name ?? connection.name,
      connector: body.connector ?? connection.connector,
      account_id: "account_id" in body ? body.account_id : connection.account_id,
      enabled: req.method === "PUT" ? body.enabled : connection.enabled,
      revision: connection.revision + 1,
      sync_interval_minutes: body.sync_interval_minutes ?? connection.sync_interval_minutes,
      products: 1,
      qualified: 1,
      last_sync_at: new Date().toISOString(),
    });
    connection.state = connection.enabled ? "idle" : "disconnected";
    product.listings[0].connection_enabled = connection.enabled;
    result = connection;
  } else if (path.endsWith("/partner-tools"))
    result = {
      items:
        connection.products &&
        (!params.get("q") || product.name.toLowerCase().includes(params.get("q").toLowerCase()))
          ? [product]
          : [],
      total:
        connection.products &&
        (!params.get("q") || product.name.toLowerCase().includes(params.get("q").toLowerCase()))
          ? 1
          : 0,
      limit: 25,
      offset: 0,
    };
  else if (path.endsWith("/actions")) {
    Object.assign(product, { excluded: body.action === "exclude", revision: product.revision + 1 });
    result = product;
  } else if (path.endsWith("/evaluations")) {
    result = [run];
  } else {
    res.writeHead(404);
    res.end("{}");
    return;
  }
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(result));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith("DEVFEED_")),
);
const app = spawn(
  process.execPath,
  [
    `${root}/node_modules/next/dist/bin/next`,
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: `${root}/apps/admin`,
    env: {
      ...env,
      DEVFEED_ADMIN_API_URL: `http://127.0.0.1:${fixture.address().port}`,
      DEVFEED_ADMIN_BASE_URL: origin,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
let browser;
const output = `${root}/reports/admin-partner-tools`;
await mkdir(output, { recursive: true });
try {
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${origin}/login`, { signal: AbortSignal.timeout(1000) });
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/partner-tools`);
  await page.waitForURL("**/partnerships/partners");
  await page.getByRole("heading", { name: "Partners", exact: true }).waitFor();
  const group = page.getByRole("region", { name: "Partnerships", exact: true });
  assert.equal(await group.getByRole("link", { name: "Partners", exact: true }).count(), 1);
  assert.equal(await group.getByRole("link", { name: "Products", exact: true }).count(), 1);
  assert.equal(
    await page
      .getByRole("region", { name: "Content", exact: true })
      .getByRole("link", { name: "Partners", exact: true })
      .count(),
    0,
  );
  await page.getByRole("link", { name: "Create partner", exact: true }).click();
  await page.waitForURL("**/partnerships/partners/new");
  await page.getByRole("combobox", { name: /Partner/ }).waitFor();
  await page.getByRole("combobox", { name: "Account", exact: true }).click();
  await page.getByPlaceholder("Search partner accounts…").fill("Alpha");
  await page.getByRole("option", { name: /Alpha account/ }).click();
  await page.getByRole("checkbox", { name: "Enabled", exact: true }).uncheck();
  await page.screenshot({ path: `${output}/create-partner-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.getByLabel("API response JSON", { exact: true }).waitFor();
  assert.ok(
    (await page.getByLabel("API response JSON", { exact: true }).textContent()).includes("slug"),
  );
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/create-partner-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  await page.getByText("Mapped Checker", { exact: true }).waitFor();
  await page.screenshot({ path: `${output}/connector-preview-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Create partner", exact: true }).click();
  await page.waitForURL("**/partnerships/partners/nick-launches");
  await page.getByText("Disabled", { exact: true }).waitFor();
  assert.equal(connection.account_id, partnerAccounts[0].id);
  assert.equal(
    await page.getByRole("link", { name: "Alpha account", exact: true }).getAttribute("href"),
    `/partnerships/accounts/${partnerAccounts[0].id}`,
  );
  assert.equal(await page.getByRole("button", { name: "Sync now" }).isDisabled(), true);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL("**/nick-launches/edit");
  assert.equal(await page.getByRole("combobox", { name: /Partner/ }).isDisabled(), true);
  await page
    .getByRole("combobox", { name: "Account", exact: true })
    .getByText("Alpha account", { exact: true })
    .waitFor();
  await page.getByRole("combobox", { name: "Account", exact: true }).click();
  await page.getByRole("option", { name: /Beta account/ }).click();
  await page.getByRole("checkbox", { name: "Enabled", exact: true }).check();
  await page.getByRole("spinbutton", { name: "Sync interval (minutes)", exact: true }).fill("90");
  await page.screenshot({ path: `${output}/partner-interval-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForURL("**/partnerships/partners/nick-launches");
  await page.getByText("Enabled", { exact: true }).last().waitFor();
  await page.getByText("Every 90 minutes", { exact: true }).waitFor();
  assert.equal(connection.account_id, partnerAccounts[1].id);
  assert.equal(
    await page.getByRole("link", { name: "Beta account", exact: true }).getAttribute("href"),
    `/partnerships/accounts/${partnerAccounts[1].id}`,
  );
  await page.reload();
  await page.getByText("Every 90 minutes", { exact: true }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByText("Record information", { exact: true }).waitFor();
  await page
    .getByRole("navigation", { name: "Object sections" })
    .getByRole("link", { name: "Details" })
    .waitFor();
  await page.screenshot({ path: `${output}/partner-detail.png`, fullPage: true });
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  await page.waitForURL("**/nick-launches/related");
  await page.getByRole("heading", { name: "Products (1)", exact: true }).waitFor();
  const relatedProducts = page.getByRole("table", { name: "Partner products", exact: true });
  for (const [header, sort] of [
    ["Name", "name"],
    ["Name", "-name"],
    ["Updated", "updated_at"],
    ["Updated", "-updated_at"],
  ]) {
    const response = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith("/partner-tools") &&
        url.searchParams.get("provider") === "nick-launches" &&
        url.searchParams.get("sort") === sort &&
        url.searchParams.get("offset") === "0"
      );
    });
    await relatedProducts.getByRole("button", { name: header, exact: true }).click();
    assert.equal((await response).status(), 200);
  }

  await page.getByRole("heading", { name: "Pipeline jobs", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Evaluations", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/partner-related-desktop.png`, fullPage: true });
  await page.getByRole("link", { name: "77777777", exact: true }).click();
  await page.waitForURL(`**/partnerships/pipeline/${childId}`);
  await page.getByText("Platform product ID", { exact: true }).waitFor();
  await page.getByText("Partner product sync completed", { exact: true }).waitFor();
  await page.screenshot({ path: `${output}/pipeline-detail-desktop.png`, fullPage: true });
  await page
    .getByRole("navigation", { name: "Object sections" })
    .getByRole("link", { name: "Logs", exact: true })
    .click();
  await page.waitForURL(`**/partnerships/pipeline/${childId}/logs`);
  await page.reload();
  await page.getByText("Partner product sync completed", { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/pipeline-logs-mobile.png`, fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${origin}/partnerships/pipeline/${parentId}/related`);
  await page.getByRole("heading", { name: "Product sync jobs", exact: true }).waitFor();
  await page.getByRole("link", { name: "77777777", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "66666666", exact: true }).count(), 0);
  await group.getByRole("link", { name: "Pipeline jobs", exact: true }).click();
  await page.getByRole("heading", { name: "Pipeline jobs", exact: true }).waitFor();
  await page.getByRole("combobox", { name: "Operation", exact: true }).click();
  await page.getByRole("option", { name: "Sync product", exact: true }).click();
  await page.waitForURL(/operation=sync_product/);
  await page.getByRole("link", { name: "77777777", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "66666666", exact: true }).count(), 0);
  await page.reload();
  await page.getByRole("link", { name: "77777777", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/pipeline-list-desktop.png`, fullPage: true });
  await page.getByRole("textbox", { name: "Search runs", exact: true }).fill("no-such-job");
  await page.getByText("No pipeline jobs found.", { exact: true }).waitFor();
  await group.getByRole("link", { name: "Evaluations", exact: true }).click();
  await page.getByRole("heading", { name: "Evaluations", exact: true }).waitFor();
  await page.getByRole("link", { name: "33333333", exact: true }).click();
  await page.waitForURL(`**/partnerships/evaluations/${run.id}`);
  await page.getByText("Partner evaluation completed", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Results", exact: true }).click();
  await page.getByText(run.result.decisions[0].reason, { exact: true }).waitFor();
  await page.getByText("Product evidence", { exact: true }).waitFor();
  await page.screenshot({ path: `${output}/evaluation-results-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/evaluation-results-mobile.png`, fullPage: true });
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  await page.getByRole("link", { name: "OpenAPI compatibility in CI", exact: true }).waitFor();
  await page.getByRole("link", { name: "Logs", exact: true }).click();
  await page.getByText("Partner evaluation completed", { exact: true }).waitFor();
  run.current = false;
  await page.goto(`${origin}/partnerships/evaluations/${run.id}/results`);
  await page
    .getByText("These results are outdated because product or article data changed.", {
      exact: true,
    })
    .waitFor();
  run.current = true;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${origin}/partnerships/partners/nick-launches/related`);
  await page
    .getByRole("navigation", { name: "Breadcrumb" })
    .getByRole("link", { name: "Partners", exact: true })
    .click();
  await page.getByRole("link", { name: "Edit Nick Launches" }).waitFor();
  assert.equal(
    await page
      .getByRole("link", { name: "Create partner", exact: true })
      .getAttribute("aria-disabled"),
    null,
  );
  await page.screenshot({ path: `${output}/partners-list.png`, fullPage: true });
  await page
    .getByRole("region", { name: "Partnerships", exact: true })
    .getByRole("link", { name: "Products", exact: true })
    .click();
  await page.getByRole("heading", { name: "Products", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: /Add product|Import|Approve|Edit product/ }).count(),
    0,
  );
  await page.getByRole("table", { name: "Products" }).waitFor();
  const assessmentColors = new Set();
  for (const [state, label, tone] of [
    ["checking", "Checking", "info"],
    ["attention", "Attention", "warning"],
    ["irrelevant", "Irrelevant", "danger"],
    ["qualified", "Qualified", "success"],
  ]) {
    product.assessment.state = state;
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    const pill = page.getByRole("table", { name: "Products" }).getByText(label, { exact: true });
    await pill.waitFor();
    assert.equal(await pill.getAttribute("data-variant"), tone);
    assessmentColors.add(await pill.evaluate((node) => getComputedStyle(node).backgroundColor));
  }
  assert.equal(assessmentColors.size, 4);

  await page.getByRole("link", { name: "API Checker", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/products-list.png`, fullPage: true });
  await page.getByRole("textbox", { name: "Search products" }).fill("missing-tool");
  await page.getByRole("textbox", { name: "Search products" }).press("Enter");
  await page.getByText("No products match your search.", { exact: true }).waitFor();
  await page.getByRole("textbox", { name: "Search products" }).fill("");
  await page.getByRole("textbox", { name: "Search products" }).press("Enter");
  await page.getByRole("link", { name: "API Checker", exact: true }).click();
  await page.waitForURL(`**/partnerships/products/${product.id}`);
  await page.getByRole("heading", { name: "API Checker", exact: true }).waitFor();
  await page.getByText("Record information", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("Qualified", { exact: true }).getAttribute("data-variant"),
    "success",
  );

  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page.getByRole("link", { name: "Related objects", exact: true }).click();
  await page.getByRole("link", { name: "View Platform B listing" }).waitFor();
  await page.getByRole("heading", { name: "Pipeline jobs", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Evaluations", exact: true }).waitFor();
  await page.screenshot({ path: `${output}/product-related.png`, fullPage: true });
  await page.getByRole("link", { name: "Details", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText("Record information", { exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Exclude product" }).click();
  await page.getByRole("button", { name: "Include and recheck" }).waitFor();
  await page.goto(`${origin}/partnerships/partners/nick-launches/edit`);
  await page.getByRole("checkbox", { name: "Enabled", exact: true }).uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.waitForURL("**/partnerships/partners/nick-launches");
  await page.getByText("Disabled", { exact: true }).waitFor();
  await page.reload();
  await page.getByText("Disabled", { exact: true }).waitFor();
  await page.goto(`${origin}/partnerships/products`);
  await page.getByRole("link", { name: "API Checker", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Include and recheck" }).isDisabled(), true);
  assert.equal(
    mutations.filter(
      (entry) =>
        !entry.path.endsWith("/connector-preview") && !entry.path.endsWith("/connector-response"),
    ).length,
    4,
  );
  assert.ok(mutations.some((entry) => entry.body.sync_interval_minutes === 90));
  assert.equal(connection.sync_interval_minutes, 90);
  assert.ok(mutations.every((entry) => entry.csrf === "fixture"));
  await page.goto(`${origin}/partnerships/partners/new`);
  await page
    .getByRole("combobox", { name: "Partner", exact: true })
    .getByText("Custom API", { exact: true })
    .waitFor();
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("Shipyard");
  await page.getByRole("textbox", { name: "Identifier", exact: true }).fill("shipyard");
  await page
    .getByRole("textbox", { name: "API base URL", exact: true })
    .fill("https://api.shipyard.example");
  await page
    .getByRole("textbox", { name: "Product list paths", exact: true })
    .fill("data.products, items");
  await page
    .getByRole("textbox", { name: "Listing URL template", exact: true })
    .fill("https://shipyard.example/products/{id}");
  await page.getByRole("combobox", { name: "Pagination mode", exact: true }).click();
  await page.getByRole("option", { name: "Offset", exact: true }).click();
  await page.getByRole("combobox", { name: "Pagination mode", exact: true }).click();
  await page.getByRole("option", { name: "Page number", exact: true }).click();
  assert.equal(
    await page.getByRole("textbox", { name: "Pagination parameter", exact: true }).inputValue(),
    "page",
  );
  await page.getByRole("combobox", { name: "Pagination mode", exact: true }).click();
  await page.getByRole("option", { name: "Offset", exact: true }).click();
  assert.equal(
    await page.getByRole("textbox", { name: "Pagination parameter", exact: true }).inputValue(),
    "offset",
  );
  await page.getByRole("textbox", { name: "Total count path", exact: true }).fill("data.total");
  await page.getByRole("checkbox", { name: "Enabled", exact: true }).uncheck();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: `${output}/custom-connector-mobile.png`, fullPage: true });
  await page.getByText("Authentication and limits", { exact: true }).click();
  await page.getByRole("combobox", { name: "Authentication", exact: true }).click();
  assert.equal(await page.getByRole("option", { name: "None", exact: true }).count(), 1);
  await page.getByRole("option", { name: "None", exact: true }).click();
  await page.getByText("Authentication and limits", { exact: true }).click();
  previewValidation = true;
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  const invalidName = page.getByRole("textbox", { name: "Product name paths", exact: true });
  await page.getByText("Use a valid response path.", { exact: true }).waitFor();
  assert.equal(await invalidName.getAttribute("aria-invalid"), "true");
  assert.equal(
    await page
      .getByRole("spinbutton", { name: "Maximum pages", exact: true })
      .getAttribute("aria-invalid"),
    "true",
  );
  assert.equal(await invalidName.evaluate((element) => element === document.activeElement), true);
  await page.screenshot({ path: `${output}/connector-validation-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "Test connection", exact: true }).click();
  await page.getByText("Mapped Checker", { exact: true }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel("API response JSON", { exact: true }).waitFor();
  const formBox = await page
    .getByRole("form", { name: "Partner settings", exact: true })
    .boundingBox();
  const responseBox = await page
    .getByRole("complementary", { name: "API response preview", exact: true })
    .boundingBox();
  assert.ok(responseBox.x >= formBox.x + formBox.width);
  await page.screenshot({ path: `${output}/custom-connector-preview-desktop.png`, fullPage: true });
  await page.getByRole("button", { name: "Create partner", exact: true }).click();
  await page.waitForURL("**/partnerships/partners/shipyard");
  await page.getByRole("heading", { name: "Shipyard", exact: true }).waitFor();
  assert.equal(customConnections.length, 1);
  assert.deepEqual(customConnections[0].connector.items_paths, ["data.products", "items"]);
  assert.equal(customConnections[0].connector.pagination.mode, "offset");
  assert.equal(customConnections[0].connector.pagination.total_path, "data.total");
  await page.reload();
  await page.getByRole("heading", { name: "Shipyard", exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log(`Partner catalog browser workflow passed. Screenshots: ${output}`);
} catch (error) {
  console.error(logs.slice(-4000));
  console.error("Product requests:", JSON.stringify(productRequests));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  fixture.close();
}

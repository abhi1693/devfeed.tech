import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. All requests use a disposable local API fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const stages = [
  "ingested",
  "enrichment",
  "analysis",
  "images",
  "review",
  "ready",
  "published",
  "rejected",
];
const labels = [
  "New feed entry",
  "Extracting article evidence",
  "Analyzing provider limits",
  "Preparing article images",
  "Editorial review needed",
  "Ready for publication",
  "Published engineering guide",
  "Rejected promotion",
];
const id = "22222222-2222-4222-8222-222222222222";
const source = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Engineering Journal",
  slug: "engineering-journal",
  source_type: "publisher",
  description: "Practical engineering articles.",
  website_url: "https://publisher.example/",
  logo_url: null,
};
let stage = "enrichment";
let imageMissing = false;
let analysisRetry = false;
let editorialRevision = 7;
let retryQueued = false;
let publishedToFeedAt = null;
let sourceGroupingDemo = false;
let analyticsPreparing = false;
let coverageState = "fresh";
let activeMaintenance = null;
let publishedJobs = null;
let oldMaintenanceDetail = false;
let oldMaintenancePost = false;
const otherSourceId = "77777777-7777-4777-8777-777777777777";
const pipelineReads = [];
const jobGenerations = { enrichment: 0, analysis: 0, images: 0 };
let posts = [];
const requests = [];
const now = "2026-10-19T09:00:00Z";
function job(key, status) {
  return {
    id: `${key === "enrichment" ? "33333333" : key === "analysis" ? "44444444" : "55555555"}-3333-4333-8333-${333333333333 + jobGenerations[key]}`,
    status,
    outcome:
      status === "succeeded"
        ? key === "images"
          ? "not_found"
          : key === "analysis"
            ? "applied"
            : "enriched"
        : null,
    available_at: now,
    finished_at: status === "succeeded" ? now : null,
    retryable: false,
  };
}
function jobs() {
  if (stage === "published" && publishedJobs) return publishedJobs;
  const index = stages.indexOf(stage);
  return {
    enrichment:
      index > 1
        ? job("enrichment", "succeeded")
        : index === 1
          ? job("enrichment", retryQueued ? "queued" : "running")
          : null,
    analysis:
      index > 2
        ? job("analysis", "succeeded")
        : index === 2
          ? job("analysis", analysisRetry || retryQueued ? "queued" : "running")
          : null,
    images: imageMissing
      ? null
      : index > 3
        ? job("images", "succeeded")
        : index === 3
          ? job("images", retryQueued ? "queued" : "running")
          : null,
  };
}
function card(cardStage = stage, index = 0) {
  return {
    id: index ? `66666666-6666-4666-8666-${String(index).padStart(12, "0")}` : id,
    title: index
      ? labels[stages.indexOf(cardStage)]
      : "Compare structured output behavior across providers",
    source_id: source.id,
    source_name: source.name,
    discovered_at: index ? "2026-10-19T08:00:00Z" : now,
    stage: cardStage,
    state: ["published", "rejected"].includes(cardStage)
      ? "complete"
      : cardStage === "ready"
        ? "ready"
        : index && cardStage === "analysis"
          ? "retry_wait"
          : index && ["enrichment", "images"].includes(cardStage)
            ? "waiting"
            : cardStage === "analysis" && analysisRetry
              ? "retry_wait"
              : cardStage === "review" || imageMissing
                ? "blocked"
                : ["enrichment", "analysis", "images"].includes(cardStage)
                  ? retryQueued
                    ? "waiting"
                    : "running"
                  : "waiting",
    blockers:
      cardStage === "ready" || ["published", "rejected"].includes(cardStage)
        ? []
        : [cardStage === "review" ? "manual_review_required" : `${cardStage}_processing`],
    next_check_at: null,
    editorial_revision: editorialRevision,
    current_analysis_step: cardStage === "analysis" ? "overview" : null,
    jobs: index ? { enrichment: null, analysis: null, images: null } : jobs(),
    allowed_actions: imageMissing ? ["advance"] : [],
    retry_targets:
      !index && ["review", "ready", "published"].includes(cardStage)
        ? ["enrichment", "analysis", "images"]
        : [],
  };
}
function board(url) {
  const attention = url.searchParams.get("needs_attention") === "true";
  const grouped = url.searchParams.get("group_by") === "source";
  const sourceGroup = url.searchParams.get("source_group");
  assert.ok(!grouped || sourceGroup === null);
  const cursor = Number(url.searchParams.get("cursor") || 0);
  const limit = Number(url.searchParams.get("limit") || 20);
  const columns = stages.map((value, index) => {
    const extra = sourceGroupingDemo
      ? Array.from({ length: value === "images" ? 21 : 1 }, (_, position) => ({
          ...card(value, 100 + index * 100 + position),
          source_id: otherSourceId,
          source_name: "Platform Journal",
          title: `Platform ${value} article ${position + 1}`,
        }))
      : [];
    if (sourceGroupingDemo && value === "images")
      extra.push({
        ...card(value, 999),
        source_id: null,
        source_name: null,
        state: "blocked",
        title: "Source attribution needs review",
      });
    const items = [card(value, index + 1), ...(value === stage ? [card()] : []), ...extra]
      .filter(
        (item) =>
          (!attention || ["blocked", "retry_wait"].includes(item.state)) &&
          (!url.searchParams.get("source_id") ||
            item.source_id === url.searchParams.get("source_id")) &&
          (sourceGroup === null ||
            (sourceGroup === "unknown" ? item.source_id === null : item.source_id === sourceGroup)),
      )
      .sort(
        (a, b) =>
          Number(b.state === "running") - Number(a.state === "running") ||
          a.discovered_at.localeCompare(b.discovered_at) ||
          a.id.localeCompare(b.id),
      );
    const groups = new Map();
    for (const item of items) {
      const group = groups.get(item.source_id) || {
        source_id: item.source_id,
        source_name: item.source_name,
        total: 0,
        state_counts: {},
      };
      group.total++;
      group.state_counts[item.state] = (group.state_counts[item.state] || 0) + 1;
      groups.set(item.source_id, group);
    }
    const groupRank = (group) =>
      group.state_counts.running
        ? 0
        : group.state_counts.blocked || group.state_counts.retry_wait
          ? 1
          : 2;
    const sourceGroups = [...groups.values()].sort(
      (a, b) =>
        groupRank(a) - groupRank(b) ||
        (a.source_name || "Unknown source").localeCompare(b.source_name || "Unknown source"),
    );
    const values = grouped ? sourceGroups : items;
    return {
      stage: value,
      total: items.length,
      items: grouped ? [] : items.slice(cursor, cursor + limit),
      source_groups: grouped ? sourceGroups.slice(cursor, cursor + limit) : [],
      next_cursor: cursor + limit < values.length ? cursor + limit : null,
    };
  });
  return {
    as_of: new Date().toISOString(),
    snapshot: {
      status: coverageState === "expired" ? "fresh" : coverageState,
      generated_at:
        coverageState === "catching_up" ? null : new Date(Date.now() - 120_000).toISOString(),
      coverage_expires_at:
        coverageState === "catching_up"
          ? null
          : new Date(Date.now() + (coverageState === "expired" ? -1000 : 480_000)).toISOString(),
      generation: String(editorialRevision),
      lag_seconds: coverageState === "catching_up" ? null : 120,
    },
    terminal_since: "2026-10-12T09:00:00Z",
    columns,
    counts: Object.fromEntries(columns.map((column) => [column.stage, column.total])),
  };
}
function analysisSteps() {
  if (stages.indexOf(stage) < 2) return [];
  return [
    ["developer_relevance", "Developer relevance"],
    ["page_kind", "Article eligibility"],
    ["language", "Source language"],
    ["content_type", "Content type"],
    ["content_format", "Content format"],
    ["topics", "Topic matching"],
    ["tags", "Tag matching"],
    ["identifying_title", "Identifying title"],
    ["overview", "Article overview"],
    ["description", "Search description"],
  ].map(([name, label], index) => ({
    name,
    label,
    status:
      stage !== "analysis" || index < 8
        ? "succeeded"
        : index === 8
          ? analysisRetry
            ? "failed"
            : "running"
          : "pending",
    prompt_version: `${name}-v1`,
    attempts: index < 8 || stage !== "analysis" ? 1 : index === 8 ? 2 : 0,
    available_at: name === "overview" && analysisRetry ? "2026-10-19T09:15:00Z" : null,
    finished_at: stage !== "analysis" || index < 8 ? now : null,
    error: name === "overview" && analysisRetry ? "Provider rate limit; retry scheduled." : null,
  }));
}
function detail() {
  return {
    article_id: id,
    editorial_revision: editorialRevision,
    stage,
    state: card().state,
    can_publish: stage === "ready",
    blockers: card().blockers,
    jobs: jobs(),
    analysis_steps: analysisSteps(),
    retry_targets: card().retry_targets,
    ...(oldMaintenanceDetail ? {} : { maintenance: activeMaintenance }),
    allowed_actions: imageMissing ? ["advance"] : [],
    advance_label: imageMissing ? "Process images" : null,
    checks: [
      {
        code: "enrichment",
        label: "Source evidence",
        status: stages.indexOf(stage) > 1 ? "passed" : "pending",
        detail: "Original article evidence is required.",
      },
      {
        code: "analysis",
        label: "Current AI analysis",
        status: stages.indexOf(stage) > 2 ? "passed" : "pending",
        detail: "Analysis must match the current source evidence.",
      },
      {
        code: "images",
        label: "Article images",
        status: stages.indexOf(stage) > 3 ? "passed" : "pending",
        detail: imageMissing
          ? "Image processing has not started."
          : stage === "images"
            ? "An image lookup is still running."
            : stages.indexOf(stage) > 3
              ? "Image lookup settled without an image; the fallback is ready."
              : "Image processing waits for article analysis.",
      },
    ],
  };
}
function article() {
  return {
    id,
    title: card().title,
    canonical_url: "https://publisher.example/structured-outputs",
    summary: "A comparison of supported provider behavior and limitations.",
    ai_summary: "A source-grounded comparison explains schema handling and refusal behavior.",
    ai_description: "Provider schema support and refusal handling.",
    author: "Engineering team",
    image_url: null,
    language: "en",
    content_type: "comparison",
    content_format: "article",
    review_status: "approved",
    publication_status: stage === "published" ? "published" : "unpublished",
    editorial_revision: editorialRevision,
    published_at: now,
    discovered_at: now,
    published_to_feed_at: stage === "published" ? now : publishedToFeedAt,
    classification_provenance: { developer_relevance: "relevant", page_kind: "article" },
    sources: [source],
    topics: [],
    tags: [],
    publication_blockers: [],
  };
}
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture");
  if (analyticsPreparing && req.method === "GET" && url.pathname === "/v1/admin/article-pipeline") {
    res.writeHead(503, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({ detail: { code: "analytics_preparing", message: "Private diagnostic" } }),
    );
    return;
  }
  requests.push(`${req.method} ${url.pathname}`);
  if (req.method === "GET" && url.pathname.startsWith("/v1/admin/article-pipeline"))
    pipelineReads.push({ path: url.pathname, query: Object.fromEntries(url.searchParams) });
  let body;
  if (url.pathname.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture-csrf",
    };
  else if (url.pathname.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  else if (url.pathname.endsWith("/notifications/config")) body = { enabled: false };
  else if (url.pathname.endsWith("/ai/connection"))
    body = {
      state: "connected",
      message: "Fixture AI connection ready",
      checked_at: now,
      quota: [],
    };
  else if (req.method === "POST") {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const payload = JSON.parse(Buffer.concat(chunks).toString());
    posts.push({ path: url.pathname, payload, csrf: req.headers["x-csrf-token"] });
    if (url.pathname.endsWith("/maintenance/retry") && oldMaintenancePost) {
      res.statusCode = 404;
      body = { detail: "Not Found" };
    } else if (
      url.pathname.endsWith("/pipeline/retry") ||
      url.pathname.endsWith("/maintenance/retry")
    ) {
      if (url.pathname.endsWith("/maintenance/retry")) assert.equal(stage, "published");
      assert.equal(payload.expected_revision, editorialRevision);
      assert.equal(
        payload.expected_job_id,
        (activeMaintenance?.jobs ?? jobs())[payload.target]?.id ?? null,
      );
      assert.ok(
        (activeMaintenance?.retry_targets ?? card().retry_targets).includes(payload.target),
      );
      if (stage === "published") {
        publishedJobs ??= jobs();
        jobGenerations[payload.target]++;
        activeMaintenance = {
          id: "88888888-8888-4888-8888-888888888888",
          article_id: id,
          target: payload.target,
          status: "active",
          version: 1,
          created_at: now,
          completed_at: null,
          ready: false,
          reasons: ["Private processing is still running."],
          allowed_actions: ["reject"],
          jobs: { [payload.target]: job(payload.target, "queued") },
          retry_targets: [],
          replacement: {
            title: "Private replacement title",
            summary: "A corrected overview prepared for review.",
            description: "Corrected source evidence.",
            image_url: "https://images.example/replacement.webp",
            language: "en",
            content_type: "tutorial",
            content_format: "article",
            topics: [{ id: source.id, name: "Python", role: "primary" }],
            tags: [{ id: source.id, name: "Async I/O" }],
          },
        };
      } else {
        stage = payload.target;
        imageMissing = false;
        analysisRetry = false;
        retryQueued = true;
        jobGenerations[payload.target]++;
      }
      body = detail();
    } else if (url.pathname.endsWith("/pipeline/advance")) {
      imageMissing = false;
      body = detail();
    } else if (url.pathname.includes("/maintenance/") && url.pathname.endsWith("/review")) {
      assert.equal(payload.expected_version, activeMaintenance.version);
      assert.equal(req.headers["x-csrf-token"], "fixture-csrf");
      assert.ok(activeMaintenance.allowed_actions.includes(payload.action));
      if (payload.action === "reject") assert.ok(payload.note.trim());
      body = {
        ...activeMaintenance,
        status: payload.action === "approve" ? "promoted" : "cancelled",
        completed_at: now,
        allowed_actions: [],
      };
      activeMaintenance = null;
    } else if (url.pathname.endsWith("/review")) {
      assert.equal(payload.action, "publish");
      stage = "published";
      publishedToFeedAt = now;
      body = article();
    } else {
      res.statusCode = 404;
      body = { detail: "Unknown fixture mutation" };
    }
  } else if (url.pathname === "/v1/admin/article-pipeline") body = board(url);
  else if (url.pathname.startsWith("/v1/admin/article-pipeline/"))
    body = board(url).columns.find((column) => column.stage === url.pathname.split("/").at(-1));
  else if (url.pathname.endsWith(`/articles/${id}/pipeline`)) body = detail();
  else if (url.pathname.endsWith(`/articles/${id}`)) body = article();
  else if (url.pathname.endsWith(`/sources/${source.id}`)) body = source;
  else body = { items: [], total: 0, limit: 25, offset: 0 };
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(body));
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
app.stdout.on("data", (chunk) => (logs += chunk));
app.stderr.on("data", (chunk) => (logs += chunk));
let browser;
const destination = `${root}/reports/article-pipeline`;
try {
  for (let attempt = 0; ; attempt++) {
    try {
      await fetch(`${origin}/login`);
      break;
    } catch {
      if (attempt > 100) throw new Error(logs);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  await mkdir(destination, { recursive: true });
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  stage = "analysis";
  analysisRetry = true;
  await page.goto(`${origin}/content/articles/${id}`);
  const stepList = page.getByRole("list", { name: "AI analysis steps" });
  await stepList.waitFor();
  await page.getByText("Current step: Article overview", { exact: true }).waitFor();
  assert.equal(await stepList.getByRole("listitem").count(), 10);
  await stepList.getByText("Waiting to retry", { exact: true }).waitFor();
  await stepList.getByText("Provider rate limit; retry scheduled.", { exact: true }).waitFor();
  assert.equal(
    await stepList
      .getByText(/Retry available/)
      .locator("time")
      .getAttribute("datetime"),
    "2026-10-19T09:15:00.000Z",
  );
  await page.screenshot({
    path: `${destination}/analysis-steps-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/analysis-steps-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  analysisRetry = false;
  stage = "images";
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(`${origin}/content/articles/${id}`);
  await page.getByText("An image lookup is still running.", { exact: true }).waitFor();
  await page.screenshot({
    path: `${destination}/readiness-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/readiness-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto(`${origin}/content/articles/${id}/review`);
  await page.getByRole("combobox", { name: /Decision/ }).click();
  await page.getByRole("option", { name: "Publish", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Apply decision" }).isDisabled(), true);
  assert.equal(posts.length, 0);
  imageMissing = true;
  await page.goto(`${origin}/content/articles/${id}`);
  await page.getByRole("button", { name: "Process images", exact: true }).click();
  await page.getByText("An image lookup is still running.", { exact: true }).waitFor();
  assert.deepEqual(posts[0], {
    path: `/v1/admin/articles/${id}/pipeline/advance`,
    payload: { expected_revision: 7 },
    csrf: "fixture-csrf",
  });
  await page.goto(`${origin}/content/articles/${id}/review`);
  await page.getByRole("combobox", { name: /Decision/ }).click();
  await page.getByRole("option", { name: "Publish", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Apply decision" }).isDisabled(), true);
  stage = "ready";
  editorialRevision = 8;
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (button) => button.textContent === "Apply decision" && !button.disabled,
    ),
  );
  await page.getByRole("button", { name: "Apply decision" }).click();
  await page.waitForURL(`${origin}/content/articles/${id}`);
  assert.equal(posts.at(-1).payload.action, "publish");
  assert.equal(posts.at(-1).payload.expected_revision, 8);
  assert.equal(posts.at(-1).csrf, "fixture-csrf");
  await page.setViewportSize({ width: 1600, height: 1100 });
  assert.deepEqual(errors, []);
  await writeFile(
    `${destination}/results.json`,
    JSON.stringify({ passed: true, workflow: "article detail and publication" }),
  );
} catch (error) {
  console.error(logs);
  console.error(requests.slice(-30));
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
}

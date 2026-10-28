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
      status: "fresh",
      generated_at: new Date(Date.now() - 120_000).toISOString(),
      generation: String(editorialRevision),
      lag_seconds: 0,
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
    if (url.pathname.endsWith("/pipeline/retry")) {
      assert.equal(payload.expected_revision, editorialRevision);
      assert.equal(payload.expected_job_id, jobs()[payload.target]?.id ?? null);
      assert.ok(card().retry_targets.includes(payload.target));
      if (stage === "published") {
        editorialRevision++;
        publishedToFeedAt = now;
      }
      stage = payload.target;
      imageMissing = false;
      analysisRetry = false;
      retryQueued = true;
      jobGenerations[payload.target]++;
      body = detail();
    } else if (url.pathname.endsWith("/pipeline/advance")) {
      imageMissing = false;
      body = detail();
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
  await page.goto(`${origin}/content/articles?view=pipeline&stage=images`);
  const focusCard = page.getByRole("link", { name: card().title, exact: true });
  await focusCard.waitFor();
  const savedStatus = page.getByRole("status").filter({ hasText: /^Updated/ });
  await savedStatus.waitFor();
  assert.ok(
    Date.now() - Date.parse(await savedStatus.locator("time").getAttribute("datetime")) > 60_000,
    "A recently verified board must retain its older last-change time without looking stale",
  );
  assert.equal(await page.getByRole("button", { name: /Refresh pipeline/ }).count(), 0);
  const transitions = [];
  const movements = [];
  async function waitForStage(next, direction) {
    const result = await page.waitForFunction(
      ({ id, next, direction }) => {
        const item = document.querySelector(`[data-pipeline-article-id="${id}"]`);
        if (item?.getAttribute("data-pipeline-stage") !== next) return false;
        if (!direction) return true;
        const animation = item.getAnimations().find((value) => value.playState === "running");
        if (!animation || !(animation.currentTime > 0)) return false;
        const frames = animation.effect.getKeyframes();
        const first = new DOMMatrix(frames[0].transform);
        const last = new DOMMatrix(frames.at(-1).transform);
        const rect = item.getBoundingClientRect();
        const bounds = item.closest('[role="region"]').getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const visible =
          x > bounds.left &&
          x < Math.min(bounds.right, innerWidth) &&
          y > bounds.top &&
          y < Math.min(bounds.bottom, innerHeight);
        if (direction === "backward" && !visible) return false;
        return {
          stage: next,
          direction,
          fromX: first.m41,
          toX: last.m41,
          currentTime: animation.currentTime,
          duration: animation.effect.getTiming().duration,
          aboveLaneBackgrounds: visible ? item.contains(document.elementFromPoint(x, y)) : null,
        };
      },
      { id, next, direction },
      { polling: "raf" },
    );
    if (direction) {
      const movement = await result.jsonValue();
      assert.ok(direction === "forward" ? movement.fromX < 0 : movement.fromX > 0);
      assert.equal(movement.toX, 0);
      assert.ok(movement.currentTime > 0 && movement.currentTime < movement.duration);
      if (movement.aboveLaneBackgrounds !== null) assert.equal(movement.aboveLaneBackgrounds, true);
      movements.push(movement);
    }
    await result.dispose();
  }
  for (const next of ["enrichment", "analysis", "images", "ready", "published"]) {
    stage = next;
    await waitForStage(next, next === "enrichment" ? null : "forward");
    assert.equal(await focusCard.count(), 1);
    transitions.push(stage);
  }
  stage = "images";
  await waitForStage("images", "backward");
  await focusCard
    .locator("xpath=ancestor::article")
    .evaluate((item) =>
      Promise.all(item.getAnimations().map((animation) => animation.finished.catch(() => {}))),
    );
  const item = focusCard.locator("xpath=ancestor::article");
  assert.equal(await item.getByRole("link").count(), 1);
  assert.equal(await item.locator("p").count(), 1);
  assert.equal(await item.locator("time").count(), 0);
  assert.equal(await item.getByLabel("Processing stages").count(), 0);
  const lane = item.locator("xpath=ancestor::section[1]");
  assert.equal(
    await lane.getByRole("article").first().getAttribute("data-pipeline-article-id"),
    id,
  );
  const statusColors = [];
  for (const [cardStage, state, hue, label] of [
    ["ingested", "waiting", "amber", "Waiting"],
    ["images", "running", "blue", "Running"],
    ["analysis", "retry_wait", "violet", "Waiting to retry"],
    ["review", "blocked", "rose", "Blocked"],
    ["ready", "ready", "emerald", "Ready"],
    ["published", "complete", "green", "Published"],
    ["rejected", "complete", "rose", "Rejected"],
  ]) {
    const colored = page
      .locator(`article[data-pipeline-stage="${cardStage}"][data-pipeline-state="${state}"]`)
      .first();
    assert.ok((await colored.getAttribute("class")).includes(`${hue}-`));
    assert.equal(await colored.getByRole("img", { name: label, exact: true }).count(), 1);
    assert.equal(
      (await colored.innerText()).replace(/\s+/g, " ").trim(),
      `${await colored.getByRole("link").innerText()} ${source.name}`,
    );
    statusColors.push({ stage: cardStage, state, hue, label });
  }
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/board-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  const imagesColumn = page.locator('section[aria-labelledby="pipeline-images"]');
  const fullWidth = (await imagesColumn.boundingBox()).width;
  await page.getByRole("heading", { name: "Images", exact: true }).dblclick();
  await page.getByRole("button", { name: "Expand Images column", exact: true }).waitFor();
  assert.equal(await imagesColumn.getAttribute("data-pipeline-collapsed"), "true");
  const railWidth = (await imagesColumn.boundingBox()).width;
  assert.ok(railWidth <= 64 && railWidth < fullWidth / 2);
  await page.getByRole("heading", { name: "Images", exact: true }).dblclick();
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).waitFor();
  assert.ok((await imagesColumn.boundingBox()).width > 200);
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).focus();
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).press("Enter");
  await page.getByRole("button", { name: "Expand Images column", exact: true }).press("Space");
  assert.equal(await imagesColumn.getAttribute("data-pipeline-collapsed"), "false");
  await page.getByRole("heading", { name: "Ingested", exact: true }).dblclick();
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.getByRole("checkbox", { name: "Rejected", exact: true }).uncheck();
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.reload();
  await page.getByRole("button", { name: "Expand Ingested column", exact: true }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Rejected", exact: true }).count(), 0);
  const preferencesPoll = await page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/admin/article-pipeline" && response.ok(),
  );
  await preferencesPoll.finished();
  assert.equal(
    await page.getByRole("button", { name: "Expand Ingested column", exact: true }).count(),
    1,
  );
  assert.equal(await page.getByRole("heading", { name: "Rejected", exact: true }).count(), 0);
  await page
    .getByRole("textbox", { name: "Search article pipeline", exact: true })
    .fill("structured");
  await page.waitForURL(/q=structured/);
  await focusCard.waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Expand Ingested column", exact: true }).count(),
    1,
  );
  await page.getByRole("textbox", { name: "Search article pipeline", exact: true }).fill("");
  await page.waitForURL((url) => !url.searchParams.has("q"));
  await focusCard.waitFor();
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).click();
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.getByRole("dialog", { name: "Pipeline columns" }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/board-columns-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("combobox", { name: "Pipeline stage", exact: true }).waitFor();
  assert.equal(await focusCard.isVisible(), true);
  assert.ok((await imagesColumn.boundingBox()).width > 250);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/board-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: /^Columns/ }).click();
  assert.equal(
    await page.getByRole("checkbox", { name: "Rejected", exact: true }).isChecked(),
    false,
  );
  await page.screenshot({
    path: `${destination}/board-columns-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.getByRole("combobox", { name: "Pipeline stage", exact: true }).click();
  assert.equal(await page.getByRole("option", { name: /^Rejected/ }).count(), 0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.getByRole("button", { name: "Show all columns", exact: true }).click();
  assert.equal(await page.getByRole("checkbox", { checked: true }).count(), stages.length);
  await page.getByRole("button", { name: /^Columns/ }).click();
  assert.equal(await imagesColumn.getAttribute("data-pipeline-collapsed"), "true");
  assert.equal(
    await page
      .locator('section[aria-labelledby="pipeline-ingested"]')
      .getAttribute("data-pipeline-collapsed"),
    "true",
  );
  await page.getByRole("combobox", { name: "Pipeline stage", exact: true }).click();
  await page.getByRole("option", { name: /^Review/ }).click();
  await page.waitForURL(/stage=review/);
  assert.equal(await focusCard.isVisible(), false);
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: "Expand Images column", exact: true }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  const settled = await page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/admin/article-pipeline" && response.ok(),
  );
  await settled.finished();
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  stage = "analysis";
  await waitForStage("analysis", null);
  assert.equal(
    await focusCard
      .locator("xpath=ancestor::article")
      .evaluate((item) => item.getAnimations().length),
    0,
  );
  await page.emulateMedia({ reducedMotion: "no-preference" });
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
  await page.goto(`${origin}/content/articles?view=pipeline&stage=published`);
  await focusCard.waitFor();
  const moveBack = page.getByRole("button", { name: `Move back ${card().title}`, exact: true });
  await moveBack.focus();
  await moveBack.press("Enter");
  for (const label of ["enrichment", "AI analysis", "images"])
    await page
      .getByRole("menuitem", { name: `Unpublish and retry ${label}`, exact: true })
      .waitFor();
  await page.screenshot({
    path: `${destination}/board-retry-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).click();
  const publishedCard = focusCard.locator("xpath=ancestor::article");
  const originalImageJobId = jobs().images.id;
  const retryPostOffset = posts.length;
  const externalTransfer = await page.evaluateHandle((id) => {
    const transfer = new DataTransfer();
    transfer.setData("application/x-devfeed-article-pipeline", id);
    transfer.setData("text/plain", id);
    return transfer;
  }, id);
  await imagesColumn.dispatchEvent("dragover", { dataTransfer: externalTransfer });
  await imagesColumn.dispatchEvent("drop", { dataTransfer: externalTransfer });
  await externalTransfer.dispose();
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  assert.equal(posts.length, retryPostOffset);
  await page
    .getByRole("region", { name: "Article publication pipeline" })
    .evaluate((region, id) => {
      const item = region.querySelector(`[data-pipeline-article-id="${id}"]`);
      region.scrollLeft += Math.max(
        0,
        item.getBoundingClientRect().right - region.getBoundingClientRect().right + 16,
      );
    }, id);
  const dragBounds = await publishedCard.boundingBox();
  await publishedCard.dragTo(page.getByRole("heading", { name: "Images", exact: true }), {
    sourcePosition: { x: 8, y: dragBounds.height - 6 },
  });
  await waitForStage("images", null);
  assert.equal(posts.length, retryPostOffset + 1);
  assert.deepEqual(posts.at(-1), {
    path: `/v1/admin/articles/${id}/pipeline/retry`,
    payload: { expected_revision: 8, expected_job_id: originalImageJobId, target: "images" },
    csrf: "fixture-csrf",
  });
  assert.equal(editorialRevision, 9);
  assert.equal(jobs().images.status, "queued");
  assert.notEqual(jobs().images.id, originalImageJobId);
  assert.equal(await imagesColumn.getAttribute("data-pipeline-collapsed"), "true");
  await page.getByRole("button", { name: "Expand Images column", exact: true }).click();
  await focusCard.waitFor({ state: "visible" });
  await page.screenshot({
    path: `${destination}/board-retry-dropped-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.goto(`${origin}/content/articles/${id}`);
  const recordInformation = page
    .getByRole("heading", { name: "Record information", exact: true })
    .locator("xpath=ancestor::section[1]");
  await recordInformation.getByText("Unpublished", { exact: true }).waitFor();
  await recordInformation.getByText("Approved", { exact: true }).waitFor();
  assert.equal(publishedToFeedAt, now);
  retryQueued = false;
  stage = "ready";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/content/articles?view=pipeline&stage=ready`);
  await focusCard.waitFor();
  await page.getByRole("button", { name: /^Columns/ }).click();
  await page.getByRole("checkbox", { name: "Enrichment", exact: true }).uncheck();
  await page.getByRole("button", { name: /^Columns/ }).click();
  const originalTextJobId = jobs().enrichment.id;
  await moveBack.focus();
  await moveBack.press("Enter");
  await page.getByRole("menuitem", { name: "Retry enrichment", exact: true }).waitFor();
  await page.screenshot({
    path: `${destination}/board-retry-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  const retrySnapshot = page.waitForResponse(async (response) => {
    if (new URL(response.url()).pathname !== "/api/v1/admin/article-pipeline" || !response.ok())
      return false;
    const snapshot = await response.json();
    return snapshot.columns.some(
      (column) =>
        column.stage === "enrichment" &&
        column.items.some((item) => item.id === id && item.jobs.enrichment?.status === "queued"),
    );
  });
  await page.getByRole("menuitem", { name: "Retry enrichment", exact: true }).focus();
  await page.getByRole("menuitem", { name: "Retry enrichment", exact: true }).press("Enter");
  const retriedBoard = await (await retrySnapshot).json();
  const retriedCard = retriedBoard.columns
    .find((column) => column.stage === "enrichment")
    .items.find((item) => item.id === id);
  assert.equal(retriedCard.jobs.enrichment.status, "queued");
  assert.deepEqual(posts.at(-1), {
    path: `/v1/admin/articles/${id}/pipeline/retry`,
    payload: { expected_revision: 9, expected_job_id: originalTextJobId, target: "enrichment" },
    csrf: "fixture-csrf",
  });
  assert.equal(editorialRevision, 9);
  retryQueued = false;
  stage = "ready";
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(`${origin}/content/articles?view=pipeline&stage=ready`);
  await focusCard.waitFor();
  const originalAnalysisJobId = jobs().analysis.id;
  await moveBack.click();
  const retryMovement = waitForStage("analysis", "backward");
  await page.getByRole("menuitem", { name: "Retry AI analysis", exact: true }).click();
  await retryMovement;
  assert.deepEqual(posts.at(-1), {
    path: `/v1/admin/articles/${id}/pipeline/retry`,
    payload: { expected_revision: 9, expected_job_id: originalAnalysisJobId, target: "analysis" },
    csrf: "fixture-csrf",
  });
  await focusCard
    .locator("xpath=ancestor::article")
    .evaluate((item) =>
      Promise.all(item.getAnimations().map((animation) => animation.finished.catch(() => {}))),
    );
  await page.screenshot({
    path: `${destination}/board-retry-queued-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  sourceGroupingDemo = true;
  retryQueued = false;
  stage = "images";
  await page.goto(`${origin}/content/articles?view=pipeline&stage=images`);
  await focusCard.waitFor();
  assert.match(await page.getByRole("combobox", { name: "Group cards" }).innerText(), /Articles/);
  await page.getByRole("combobox", { name: "Group cards" }).click();
  await page.getByRole("option", { name: "Source", exact: true }).click();
  await page.waitForURL((url) => url.searchParams.get("group_by") === "source");
  const engineeringGroup = imagesColumn.getByRole("group", {
    name: "Engineering Journal source group",
    exact: true,
  });
  const platformGroup = imagesColumn.getByRole("group", {
    name: "Platform Journal source group",
    exact: true,
  });
  const unknownGroup = imagesColumn.getByRole("group", { name: "Unknown source source group" });
  await engineeringGroup
    .getByLabel("2 articles from Engineering Journal", { exact: true })
    .waitFor();
  await engineeringGroup.getByLabel("1 Running", { exact: true }).waitFor();
  await engineeringGroup.getByLabel("1 Waiting", { exact: true }).waitFor();
  await platformGroup.getByLabel("21 Waiting", { exact: true }).waitFor();
  await unknownGroup.getByLabel("1 Blocked", { exact: true }).waitFor();
  assert.equal(await imagesColumn.getByLabel("24 articles", { exact: true }).count(), 1);
  assert.deepEqual(
    await imagesColumn
      .getByRole("group")
      .evaluateAll((groups) =>
        groups.map((group) => group.getAttribute("data-pipeline-source-id")),
      ),
    [source.id, "unknown", otherSourceId],
  );
  assert.equal(await focusCard.count(), 0);
  assert.equal(await engineeringGroup.getAttribute("draggable"), null);
  assert.equal(await engineeringGroup.getByRole("button", { name: /Move back/ }).count(), 0);
  await page.screenshot({
    path: `${destination}/source-groups-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  const sourcePoll = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith("/article-pipeline/images") &&
      url.searchParams.get("source_group") === source.id &&
      response.ok()
    );
  });
  await engineeringGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .focus();
  await engineeringGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .press("Enter");
  await sourcePoll;
  await focusCard.waitFor();
  assert.equal(await focusCard.locator("xpath=ancestor::article").locator("p").count(), 0);
  const pollReadOffset = pipelineReads.length;
  await page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith("/article-pipeline/images") &&
      url.searchParams.get("source_group") === source.id &&
      response.ok()
    );
  });
  assert.equal(
    await engineeringGroup
      .getByRole("button", { name: "Collapse Engineering Journal articles" })
      .getAttribute("aria-expanded"),
    "true",
  );
  assert.equal(await focusCard.count(), 1);
  assert.ok(pipelineReads.length > pollReadOffset);
  await unknownGroup.getByRole("button", { name: "Expand Unknown source articles" }).click();
  await unknownGroup.getByRole("link", { name: "Source attribution needs review" }).waitFor();
  await platformGroup.getByRole("button", { name: "Expand Platform Journal articles" }).click();
  await platformGroup
    .getByRole("link", { name: "Platform images article 20", exact: true })
    .waitFor();
  assert.equal(await platformGroup.getByRole("link").count(), 20);
  await platformGroup.getByRole("button", { name: "Load more Platform Journal articles" }).click();
  await platformGroup
    .getByRole("link", { name: "Platform images article 21", exact: true })
    .waitFor();
  assert.equal(await platformGroup.getByRole("link").count(), 21);
  await page.waitForFunction((sourceId) => {
    const group = document.querySelector(
      `[data-pipeline-source-id="${sourceId}"][data-pipeline-stage="images"]`,
    );
    return group?.querySelectorAll("article").length === 20;
  }, otherSourceId);
  assert.equal(
    await platformGroup
      .getByRole("button", { name: "Collapse Platform Journal articles" })
      .getAttribute("aria-expanded"),
    "true",
  );
  await unknownGroup.getByRole("button", { name: "Collapse Unknown source articles" }).click();
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  assert.equal(
    await platformGroup
      .locator("article")
      .evaluateAll((items) =>
        items.reduce((total, item) => total + item.getAnimations().length, 0),
      ),
    0,
  );
  await platformGroup.getByRole("button", { name: "Collapse Platform Journal articles" }).click();
  await page.screenshot({
    path: `${destination}/source-groups-expanded-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await engineeringGroup
    .getByRole("button", { name: "Collapse Engineering Journal articles" })
    .focus();
  await engineeringGroup
    .getByRole("button", { name: "Collapse Engineering Journal articles" })
    .press("Enter");
  await engineeringGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .press("Enter");
  await focusCard.waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({
    path: `${destination}/source-groups-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  stage = "ready";
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto(`${origin}/content/articles?view=pipeline&group_by=source&stage=ready`);
  const readyGroup = page
    .locator('section[aria-labelledby="pipeline-ready"]')
    .getByRole("group", { name: "Engineering Journal source group", exact: true });
  await readyGroup.getByRole("button", { name: "Expand Engineering Journal articles" }).click();
  await focusCard.waitFor();
  await moveBack.click();
  await page.getByRole("menuitem", { name: "Retry images", exact: true }).waitFor();
  await page.screenshot({
    path: `${destination}/source-groups-actions-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Collapse Images column", exact: true }).click();
  const groupedImageBaseline = jobs().images.id;
  const groupedRetryOffset = posts.length;
  const groupedCard = focusCard.locator("xpath=ancestor::article");
  const groupedBounds = await groupedCard.boundingBox();
  const groupedRetrySnapshot = page.waitForResponse(async (response) => {
    const url = new URL(response.url());
    if (!url.pathname.endsWith("/article-pipeline") || !response.ok()) return false;
    const snapshot = await response.json();
    return snapshot.columns
      .find((column) => column.stage === "images")
      ?.source_groups.some(
        (group) =>
          group.source_id === source.id && group.total === 2 && group.state_counts.waiting === 2,
      );
  });
  await groupedCard.dragTo(page.getByRole("heading", { name: "Images", exact: true }), {
    sourcePosition: { x: 8, y: groupedBounds.height - 6 },
  });
  await groupedRetrySnapshot;
  assert.equal(posts.length, groupedRetryOffset + 1);
  assert.deepEqual(posts.at(-1), {
    path: `/v1/admin/articles/${id}/pipeline/retry`,
    payload: { expected_revision: 9, expected_job_id: groupedImageBaseline, target: "images" },
    csrf: "fixture-csrf",
  });
  await page.getByRole("button", { name: "Expand Images column", exact: true }).click();
  await engineeringGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .click();
  await focusCard.waitFor();
  await page.screenshot({
    path: `${destination}/source-groups-dropped-desktop.png`,
    fullPage: true,
    animations: "disabled",
  });
  retryQueued = false;
  stage = "ready";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/content/articles?view=pipeline&group_by=source&stage=ready`);
  await readyGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .press("Enter");
  await focusCard.waitFor();
  await moveBack.press("Enter");
  const groupedTextBaseline = jobs().enrichment.id;
  const groupedTextRetry = page.waitForResponse(
    (response) => new URL(response.url()).pathname.endsWith("/pipeline/retry") && response.ok(),
  );
  await page.getByRole("menuitem", { name: "Retry enrichment", exact: true }).press("Enter");
  await groupedTextRetry;
  assert.deepEqual(posts.at(-1).payload, {
    expected_revision: 9,
    expected_job_id: groupedTextBaseline,
    target: "enrichment",
  });
  stage = "images";
  retryQueued = false;
  await page.goto(
    `${origin}/content/articles?view=pipeline&group_by=source&stage=images&source_id=${source.id}`,
  );
  await engineeringGroup
    .getByRole("button", { name: "Expand Engineering Journal articles" })
    .click();
  await focusCard.waitFor();
  assert.equal(await imagesColumn.getByRole("group").count(), 1);
  assert.ok(
    pipelineReads.some(
      (read) =>
        read.path.endsWith("/images") &&
        read.query.source_group === source.id &&
        read.query.source_id === source.id &&
        !read.query.group_by &&
        read.query.limit === "20",
    ),
  );
  await page.screenshot({
    path: `${destination}/source-groups-filtered-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("combobox", { name: "Group cards" }).click();
  await page.getByRole("option", { name: "Articles", exact: true }).click();
  await page.waitForURL(
    (url) => !url.searchParams.has("group_by") && url.searchParams.get("source_id") === source.id,
  );
  await focusCard.waitFor();
  assert.equal(await imagesColumn.getByRole("group").count(), 0);
  await page.bringToFront();
  assert.ok(await page.locator("article[data-board-item-id]").count());
  analyticsPreparing = true;
  await page
    .getByRole("region", { name: "Article publication pipeline" })
    .waitFor({ state: "detached" });
  await page
    .getByText("Analytics are being prepared. Please retry shortly.", { exact: true })
    .first()
    .waitFor();
  assert.equal(await page.locator("article[data-board-item-id]").count(), 0);
  assert.equal(await page.getByText("Private diagnostic", { exact: true }).count(), 0);
  analyticsPreparing = false;
  await page.getByRole("region", { name: "Article publication pipeline" }).waitFor();
  assert.ok(await page.locator("article[data-board-item-id]").count());
  assert.deepEqual(errors, []);
  await writeFile(
    `${destination}/summary.json`,
    JSON.stringify(
      {
        transitions,
        movements,
        reducedMotionRespected: true,
        statusColors,
        runningCardsFirst: true,
        columnPreferences: {
          fullWidth,
          railWidth,
          reloadAndPollPersisted: true,
          keyboardToggles: true,
          hiddenMobileOptions: true,
          mobileCollapseIgnored: true,
          showAllPreservesCollapse: true,
        },
        automaticStageUpdates: true,
        preparationWithdrawsPreviousBoardAndRecoversAutomatically: true,
        automaticReviewRevisionUpdates: true,
        minimalCards: true,
        retryActions: posts.filter((post) => post.path.endsWith("/pipeline/retry")),
        publishedRetryUnpublished: true,
        hiddenStageKeyboardRetry: true,
        serverRetryBackwardAnimation: true,
        sourceGrouping: {
          collapsedSummaries: true,
          exactArticleAndStatusCounts: true,
          runningSourcesFirst: true,
          boundedPaginationResetsOnPoll: true,
          expansionSurvivesPolling: true,
          accordionDoesNotAnimateArticles: true,
          keyboardAndMobile: true,
          individualDragAndRetry: true,
          sourceFilterRetained: true,
          unknownGroupExplicit: true,
        },
        externalDropIgnored: true,
        publicationBlockedWhileImagesRunning: true,
        recoveryCsrfChecked: true,
        analysisStepRetryVisible: true,
        publishedAfterReady: true,
        screenshots: [
          "board-desktop.png",
          "board-mobile.png",
          "board-columns-desktop.png",
          "board-columns-mobile.png",
          "board-retry-desktop.png",
          "board-retry-mobile.png",
          "board-retry-dropped-desktop.png",
          "board-retry-queued-desktop.png",
          "readiness-desktop.png",
          "readiness-mobile.png",
          "analysis-steps-desktop.png",
          "analysis-steps-mobile.png",
          "source-groups-desktop.png",
          "source-groups-expanded-desktop.png",
          "source-groups-mobile.png",
          "source-groups-actions-desktop.png",
          "source-groups-dropped-desktop.png",
          "source-groups-filtered-mobile.png",
        ],
      },
      null,
      2,
    ),
  );
  console.log(
    "Article pipeline browser checks passed: stage movement, mobile lane navigation, readiness gating, revision/CSRF recovery, and publication after completion.",
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

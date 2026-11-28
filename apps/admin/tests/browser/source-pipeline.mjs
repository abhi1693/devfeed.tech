import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. No production services or inference are used.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const stages = [
  "discovery",
  "profile",
  "assessment",
  "branding",
  "review",
  "ingestion",
  "active",
  "paused",
  "rejected",
];
const labels = [
  "Discovery",
  "Profile",
  "AI assessment",
  "Branding",
  "Review",
  "Ingestion",
  "Active",
  "Paused",
  "Rejected",
];
const uuid = (value) => `11111111-1111-4111-8111-${String(value).padStart(12, "0")}`;
const id = uuid(1);
const candidateId = uuid(2);
const now = "2026-10-19T09:00:00Z";
let stage = "profile";
let generation = 1;
let enabled = false;
let approved = false;
let queued = false;
let linked = false;
let conflict = false;
let profileMissing = false;
let analyticsPreparing = false;
let coverageState = "fresh";
const posts = [];
const reads = [];
const snapshot = () => generation.toString(16).padStart(64, "0");
const jobId = (target) => uuid(1000 + stages.indexOf(target) * 100 + generation);
function card(value = stage, index = 0) {
  const terminal = ["active", "paused", "rejected"].includes(value);
  return {
    key: `source:${index ? uuid(index + 10) : id}`,
    kind: "source",
    id: index ? uuid(index + 10) : id,
    source_id: index ? uuid(index + 10) : id,
    candidate_id: null,
    name: index ? `${labels[stages.indexOf(value)]} source` : "Engineering Journal",
    website_url: "https://publisher.example/",
    feed_url: "https://publisher.example/feed.xml",
    source_type: "publisher",
    approval_status: index
      ? terminal && value !== "rejected"
        ? "approved"
        : value === "rejected"
          ? "rejected"
          : "pending"
      : value === "rejected"
        ? "rejected"
        : approved
          ? "approved"
          : "pending",
    enabled: index ? value === "active" : enabled,
    stage: value,
    state: terminal
      ? "complete"
      : value === "review"
        ? "blocked"
        : index
          ? value === "assessment"
            ? "retry_wait"
            : "waiting"
          : queued || profileMissing
            ? "waiting"
            : "running",
    created_at: index ? "2026-10-18T08:00:00Z" : now,
    updated_at: now,
    snapshot: snapshot(),
    can_advance: !index && (value === "paused" || profileMissing),
    can_approve: value === "review",
    can_ingest: !index && approved && enabled && !(value === "ingestion" && queued),
    next_action:
      !index && value === "paused"
        ? "resume"
        : !index && profileMissing
          ? value === "rejected"
            ? "reopen"
            : "profile"
          : null,
    advance_label:
      !index && value === "paused"
        ? "Resume"
        : !index && profileMissing
          ? value === "rejected"
            ? "Reopen for review"
            : "Look up profile"
          : null,
    retry_targets:
      index || ["profile", "discovery", "rejected"].includes(value)
        ? []
        : ["profile", "assessment", "branding", ...(approved ? ["ingestion"] : [])].map(
            (target) => ({ stage: target, job_id: jobId(target) }),
          ),
  };
}
function candidate() {
  return {
    ...card("discovery", 1),
    key: `candidate:${candidateId}`,
    kind: "candidate",
    id: candidateId,
    source_id: null,
    candidate_id: candidateId,
    name: "New developer publication",
    website_url: "https://candidate.example/",
    feed_url: null,
    state: "blocked",
    retry_targets: [{ stage: "discovery", job_id: null }],
  };
}
function board(url) {
  const columns = stages.map((value, index) => {
    let items = [
      card(value, index + 1),
      ...(value === stage ? [card()] : []),
      ...(value === "discovery" && !linked ? [candidate()] : []),
    ];
    const q = url.searchParams.get("q");
    if (q) items = items.filter((item) => item.name.toLowerCase().includes(q.toLowerCase()));
    if (url.searchParams.get("needs_attention") === "true")
      items = items.filter((item) => ["blocked", "retry_wait"].includes(item.state));
    items.sort(
      (a, b) =>
        Number(b.state === "running") - Number(a.state === "running") ||
        a.created_at.localeCompare(b.created_at) ||
        a.key.localeCompare(b.key),
    );
    return { stage: value, total: items.length, items, next_cursor: null };
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
      generation: String(generation),
      lag_seconds: coverageState === "catching_up" ? null : 120,
    },
    terminal_since: "2026-10-12T09:00:00Z",
    counts: Object.fromEntries(columns.map((column) => [column.stage, column.total])),
    columns,
  };
}
function detail(kind = "source") {
  const item = kind === "candidate" && !linked ? candidate() : card();
  return {
    item,
    checks: [
      {
        key: "profile",
        status: profileMissing ? "missing" : "succeeded",
        job_id: profileMissing ? null : jobId("profile"),
        error: null,
        available_at: now,
      },
      {
        key: "branding",
        status: stage === "branding" ? "running" : "not_found",
        job_id: jobId("branding"),
        error: null,
        available_at: now,
      },
    ],
    blockers:
      item.kind === "candidate"
        ? ["feed_discovery_failed"]
        : stage === "branding"
          ? ["branding_processing"]
          : profileMissing
            ? ["profile_missing"]
            : [],
    tasks: [
      {
        stage: "profile",
        job_id: jobId("profile"),
        status: "succeeded",
        attempts: 1,
        created_at: now,
        available_at: now,
        finished_at: now,
        error: null,
      },
      {
        stage: "assessment",
        job_id: jobId("assessment"),
        status: "failed",
        attempts: 3,
        created_at: now,
        available_at: now,
        finished_at: now,
        error: "Assessment provider unavailable; manual review remains available.",
      },
    ],
    profile: {
      description: "Practical engineering reporting.",
      website_url: item.website_url,
      logo_url: null,
      image_url: null,
      language: "en",
    },
    assessment: null,
    reviews: [],
    candidate_ids: linked ? [candidateId] : [],
  };
}
function source() {
  return {
    id,
    name: "Engineering Journal",
    slug: "engineering-journal",
    source_type: "publisher",
    website_url: "https://publisher.example/",
    feed_url: "https://publisher.example/feed.xml",
    description: "Practical engineering reporting.",
    logo_url: null,
    image_url: null,
    language: "en",
    approval_status: approved ? "approved" : "pending",
    enabled,
    relevance_assessment: {},
    full_automation: false,
    publication_policy: "manual",
    publication_policy_revision: 0,
    submitted_by: null,
    submission_channel: "admin",
    reviewed_at: approved ? now : null,
    reviewed_by: null,
    review_note: null,
    metadata_enriched_at: now,
    metadata_error: null,
    updated_at: now,
    created_at: now,
    polling_mode: "fixed",
    adaptive_polling_enabled: false,
    effective_polling_mode: "fixed",
    effective_interval_seconds: 43200,
    polling_state: {},
    poll_interval_seconds: 43200,
    next_fetch_at: now,
    last_attempt_at: null,
    last_success_at: null,
    consecutive_failures: 0,
    last_error: null,
  };
}
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture");
  if (analyticsPreparing && req.method === "GET" && url.pathname === "/v1/admin/source-pipeline") {
    res.writeHead(503, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({ detail: { code: "analytics_preparing", message: "Private diagnostic" } }),
    );
    return;
  }
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
    body = { state: "connected", message: "Connected", checked_at: now, quota: [] };
  else if (url.pathname.startsWith("/v1/admin/source-pipeline")) {
    reads.push({ path: url.pathname, query: Object.fromEntries(url.searchParams) });
    if (req.method === "POST") {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString());
      posts.push({ path: url.pathname, payload, csrf: req.headers["x-csrf-token"] });
      if (conflict) {
        conflict = false;
        generation++;
        res.statusCode = 409;
        body = { detail: "Source processing changed. Refresh and try again." };
      } else if (payload.expected_snapshot !== snapshot()) {
        res.statusCode = 409;
        body = { detail: "Stale source snapshot" };
      } else {
        if (url.pathname.endsWith("/retry")) {
          assert.equal(payload.job_id, jobId(payload.target));
          assert.ok(card().retry_targets.some((target) => target.stage === payload.target));
          if (payload.target !== "ingestion" && enabled && approved) enabled = false;
          stage = payload.target;
          queued = true;
        } else if (url.pathname.endsWith("/advance")) {
          if (stage === "paused") {
            enabled = true;
            stage = "ingestion";
            queued = true;
          } else if (stage === "rejected") {
            stage = "profile";
            queued = true;
            approved = enabled = false;
          }
          profileMissing = false;
        }
        generation++;
        body = detail();
      }
    } else if (url.pathname.includes("/items/"))
      body = detail(url.pathname.includes("/candidate/") ? "candidate" : "source");
    else if (url.pathname.includes("/columns/"))
      body = board(url).columns.find((column) => column.stage === url.pathname.split("/").at(-1));
    else body = board(url);
  } else if (url.pathname === `/v1/admin/sources/${id}`) body = source();
  else if (url.pathname === "/v1/admin/sources")
    body = { items: [source()], total: 1, limit: 25, offset: 0 };
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
const destination = `${root}/reports/source-pipeline`;
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
  await page.goto(`${origin}/content/sources?view=pipeline`);
  await page.getByRole("heading", { name: "Sources", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Pipeline", exact: true }).count(), 0);
  await page.goto(`${origin}/content/sources/${id}`);
  await page.getByRole("heading", { name: "Source readiness", exact: true }).waitFor();
  await page
    .getByRole("list", { name: "Source processing runs" })
    .getByText("Assessment provider unavailable; manual review remains available.", { exact: true })
    .waitFor();
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
  assert.deepEqual(errors, []);
  await writeFile(
    `${destination}/results.json`,
    JSON.stringify({ passed: true, workflow: "source list and readiness" }),
  );
} catch (cause) {
  console.error(logs);
  throw cause;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
}

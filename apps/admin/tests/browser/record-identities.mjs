import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

// Run after npm run admin:build. Every API write targets this disposable fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const sharedId = "22222222-2222-4222-8222-222222222222";
const topicId = "11111111-1111-4111-8111-111111111111";
const relatedTopicId = "33333333-3333-4333-8333-333333333333";
const date = "2026-10-15T10:00:00Z";
const requests = [];
const unexpected = [];
const writes = [];
const runs = ["analysis", "topic-analysis"].map((kind) => ({
  id: sharedId,
  kind,
  target_name: kind === "analysis" ? "Article subject" : "Topic subject",
  status: "failed",
  retryable: true,
  attempts: 1,
  created_at: date,
  available_at: date,
  finished_at: date,
  error: "Fixture failure",
  details: { pipeline: kind },
}));
const enrichmentRun = {
  ...runs[0],
  kind: "article-enrichment",
  target_name: "Enrichment subject",
};
const topics = [
  { id: topicId, name: "Frontend", slug: "frontend" },
  { id: relatedTopicId, name: "Runtime", slug: "runtime" },
].map((topic) => ({
  ...topic,
  kind: "technology",
  status: "active",
  aliases: [],
  keywords: [],
  facts: [],
}));
const relations = [
  { topic_id: topicId, related_topic_id: relatedTopicId },
  { topic_id: relatedTopicId, related_topic_id: topicId },
].map((relation, index) => ({
  ...relation,
  relation: "depends_on",
  evidence_url: `https://example.com/evidence/${index === 0 ? "forward" : "reverse"}`,
}));
const apiPath = (relation) =>
  `/v1/admin/topic-relations/${relation.topic_id}/${relation.related_topic_id}/${relation.relation}`;
const uiPath = (relation) =>
  `/taxonomy/relationships/${relation.topic_id}/${relation.relation}/${relation.related_topic_id}`;
const relationRecords = new Map(relations.map((relation) => [apiPath(relation), relation]));
const pageOf = (items) => ({ items, total: items.length, offset: 0, limit: 25 });
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const pathname = url.pathname;
  requests.push({ method: req.method, pathname });
  let body;
  let status = 200;
  if (pathname === "/v1/admin/auth/me") {
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  } else if (pathname === "/v1/admin/auth/config") body = { enabled: true, providers: [] };
  else if (pathname === "/v1/admin/ai/connection")
    body = { state: "disabled", message: "Disabled", quota: [] };
  else if (pathname === "/v1/admin/settings") {
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  } else if (pathname === "/v1/admin/notifications/config") body = { enabled: false };
  else if (pathname === "/v1/admin/jobs/ai-analysis") {
    const type = url.searchParams.get("analysis_type");
    body = pageOf(
      runs.filter(
        (run) => !type || run.kind === (type === "topics" ? "topic-analysis" : "analysis"),
      ),
    );
  } else if (pathname === "/v1/admin/topics") body = pageOf(topics);
  else if (topics.some((topic) => pathname === `/v1/admin/topics/${topic.id}`))
    body = topics.find((topic) => pathname === `/v1/admin/topics/${topic.id}`);
  else if (pathname === "/v1/admin/topic-relationships") {
    body = pageOf(
      [...relationRecords.values()].map((relation) => ({
        ...relation,
        topic_name: topics.find((topic) => topic.id === relation.topic_id).name,
        related_topic_name: topics.find((topic) => topic.id === relation.related_topic_id).name,
        status: "approved",
        proposal: null,
      })),
    );
  } else if (relationRecords.has(pathname)) {
    const relation = relationRecords.get(pathname);
    if (req.method === "PUT") {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString());
      writes.push({ method: req.method, pathname, body: payload });
      assert.equal(payload.topic_id, relation.topic_id);
      assert.equal(payload.related_topic_id, relation.related_topic_id);
      assert.equal(payload.relation, relation.relation);
      assert.equal(req.headers["x-csrf-token"], "fixture");
      Object.assign(relation, payload);
    } else if (req.method === "DELETE") {
      writes.push({ method: req.method, pathname });
      assert.equal(req.headers["x-csrf-token"], "fixture");
      relationRecords.delete(pathname);
      status = 204;
    }
    body = relation;
  } else {
    const run = [...runs, enrichmentRun].find((candidate) => {
      const base = `/v1/admin/jobs/${candidate.kind}/${sharedId}`;
      return pathname === base || pathname === `${base}/logs` || pathname === `${base}/retry`;
    });
    if (run && pathname.endsWith("/logs"))
      body = {
        items: Array.from({ length: 80 }, (_, index) => ({
          id: `${index + 1}-0`,
          level: "INFO",
          timestamp: date,
          message: index === 0 ? `${run.kind} log` : `${run.kind} entry ${index + 1}`,
          fields: {},
        })),
        next_cursor: "1-0",
        has_more: false,
        truncated: false,
        unreadable_entries: 0,
        retention_seconds: 604800,
        max_entries: 1000,
        job_status: "failed",
        attempts: 1,
      };
    else if (run && pathname.endsWith("/retry")) {
      writes.push({ method: req.method, pathname });
      assert.equal(req.method, "POST");
      assert.equal(req.headers["x-csrf-token"], "fixture");
      body = { ...run, status: "queued" };
    } else if (run) body = run;
    else {
      unexpected.push({ method: req.method, pathname });
      status = 404;
      body = { detail: "Unknown fixture endpoint" };
    }
  }
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.end(status === 204 ? undefined : JSON.stringify(body));
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
try {
  let started = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const response = await fetch(`${origin}/login`);
      if (response.ok) {
        started = true;
        break;
      }
    } catch {
      /* Server is still starting. */
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(started, "Admin fixture server started");
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  async function assertBreadcrumbs(trail) {
    assert.deepEqual(
      await breadcrumb.getByRole("link").evaluateAll((links) =>
        links.map((link) => ({
          label: link.textContent.trim(),
          href: link.getAttribute("href"),
        })),
      ),
      [{ label: "Home", href: "/" }, ...trail],
    );
  }
  const enrichmentCards = [
    { label: "Article enrichment", href: "/jobs/enrichment/articles" },
    { label: "Images", href: "/jobs/enrichment/images" },
    { label: "Source enrichment", href: "/jobs/enrichment/sources" },
  ];
  const groups = [
    {
      path: "/content",
      title: "Content",
      cards: [
        { label: "Articles", href: "/content/articles" },
        { label: "Sources", href: "/content/sources" },
      ],
      trail: [],
    },
    {
      path: "/taxonomy",
      title: "Taxonomy",
      cards: [
        { label: "Topics", href: "/taxonomy/topics" },
        { label: "Tags", href: "/taxonomy/tags" },
        { label: "Topic relationships", href: "/taxonomy/relationships" },
      ],
      trail: [],
    },
    {
      path: "/jobs",
      title: "Jobs",
      cards: [
        { label: "Feed ingestion", href: "/jobs/ingestion" },
        ...enrichmentCards,
        { label: "AI analysis", href: "/jobs/analysis" },
        { label: "Notification delivery", href: "/jobs/notifications" },
      ],
      trail: [],
    },
    {
      path: "/jobs/enrichment",
      title: "Enrichment jobs",
      cards: enrichmentCards,
      trail: [{ label: "Jobs", href: "/jobs" }],
    },
  ];
  for (const group of groups) {
    await page.goto(`${origin}${group.path}`);
    await page.getByRole("heading", { name: group.title, exact: true, level: 1 }).waitFor();
    await page.waitForFunction(
      (expected) => document.title === `${expected} · DevFeed Admin`,
      group.title,
    );
    const cards = await page.locator("main section > div.grid > a").evaluateAll((links) =>
      links.map((link) => ({
        label: link.querySelector("div").textContent.trim(),
        href: link.getAttribute("href"),
      })),
    );
    assert.deepEqual(cards, group.cards, `${group.title} shows exactly its declared resources`);
    assert.equal(new Set(cards.map((card) => card.label)).size, cards.length);
    await assertBreadcrumbs(group.trail);
  }
  await breadcrumb.getByRole("link", { name: "Jobs", exact: true }).click();
  await page.waitForURL(`${origin}/jobs`);
  await page.getByRole("heading", { name: "Jobs", exact: true, level: 1 }).waitFor();

  await page.goto(`${origin}/jobs/enrichment/articles/${sharedId}`);
  await page.getByRole("heading", { name: `Run ${sharedId.slice(0, 8)}`, exact: true }).waitFor();
  await assertBreadcrumbs([
    { label: "Jobs", href: "/jobs" },
    { label: "Enrichment jobs", href: "/jobs/enrichment" },
    { label: "Article enrichment", href: "/jobs/enrichment/articles" },
  ]);
  await breadcrumb.getByRole("link", { name: "Enrichment jobs", exact: true }).click();
  await page.waitForURL(`${origin}/jobs/enrichment`);
  await page.getByRole("heading", { name: "Enrichment jobs", exact: true, level: 1 }).waitFor();

  for (const [section, kind, label] of [
    ["articles", "analysis", "Article"],
    ["topics", "topic-analysis", "Topic"],
  ]) {
    const path = `/jobs/analysis/${section}/${sharedId}`;
    await page.goto(`${origin}${path}`);
    await page.getByRole("heading", { name: `Run ${sharedId.slice(0, 8)}`, exact: true }).waitFor();
    await page.waitForFunction(
      (expected) => document.title.includes(expected),
      `${label} AI analysis`,
    );
    await assertBreadcrumbs([
      { label: "Jobs", href: "/jobs" },
      { label: "AI analysis", href: "/jobs/analysis" },
    ]);
    assert.equal(
      await page.getByRole("link", { name: "Logs", exact: true }).getAttribute("href"),
      `${path}/logs`,
    );
    assert.equal(
      await page.getByRole("link", { name: "Details", exact: true }).getAttribute("href"),
      path,
    );
    await page.getByRole("link", { name: "Logs", exact: true }).click();
    await page.waitForURL(`${origin}${path}/logs`);
    await page.getByText(`${kind} log`, { exact: true }).waitFor();
    const logViewport = page.getByRole("region", { name: "Runtime log entries", exact: true });
    await page.getByRole("textbox", { name: "Search logs", exact: true }).focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    assert.equal(await logViewport.evaluate((node) => document.activeElement === node), true);
    assert.equal(await logViewport.evaluate((node) => node.scrollHeight > node.clientHeight), true);
    await page.keyboard.press("PageDown");
    await page.waitForFunction(
      () => document.querySelector('[aria-label="Runtime log entries"]').scrollTop > 0,
    );
    await mkdir("/tmp/devfeed-sonar-a11y/admin", { recursive: true });
    await page.screenshot({
      path: `/tmp/devfeed-sonar-a11y/admin/${kind}-log-keyboard.png`,
      animations: "disabled",
    });
    await page.waitForFunction(
      (expected) => document.title.startsWith("Logs") && document.title.includes(expected),
      `${label} AI analysis`,
    );
    assert.ok(
      requests.some((request) => request.pathname === `/v1/admin/jobs/${kind}/${sharedId}`),
    );
    assert.ok(
      requests.some((request) => request.pathname === `/v1/admin/jobs/${kind}/${sharedId}/logs`),
    );
  }

  await page.goto(`${origin}/jobs/analysis`);
  const table = page.getByRole("table", { name: "AI analysis" });
  await table.waitFor();
  const articleRow = table
    .getByRole("row")
    .filter({ has: page.getByText("Article", { exact: true }) });
  const topicRow = table.getByRole("row").filter({ has: page.getByText("Topic", { exact: true }) });
  assert.equal(
    await articleRow
      .getByRole("link", { name: sharedId.slice(0, 8), exact: true })
      .getAttribute("href"),
    `/jobs/analysis/articles/${sharedId}`,
  );
  assert.equal(
    await topicRow
      .getByRole("link", { name: sharedId.slice(0, 8), exact: true })
      .getAttribute("href"),
    `/jobs/analysis/topics/${sharedId}`,
  );
  await page.getByRole("checkbox", { name: "Select all on this page" }).check();
  await page
    .getByRole("region", { name: "Selected rows" })
    .getByRole("button", { name: "Retry", exact: true })
    .click();
  await page.getByRole("button", { name: "Confirm retry", exact: true }).click();
  await page.getByText("Retry: 2 completed", { exact: true }).waitFor();
  assert.deepEqual(
    writes
      .filter((write) => write.pathname.endsWith("/retry"))
      .map((write) => write.pathname)
      .sort(),
    [
      `/v1/admin/jobs/analysis/${sharedId}/retry`,
      `/v1/admin/jobs/topic-analysis/${sharedId}/retry`,
    ],
  );

  for (const [index, relation] of relations.entries()) {
    const path = uiPath(relation);
    await page.goto(`${origin}${path}/edit`);
    await page.getByRole("heading", { name: "Edit topic relationship", exact: true }).waitFor();
    await assertBreadcrumbs([
      { label: "Taxonomy", href: "/taxonomy" },
      { label: "Topic relationships", href: "/taxonomy/relationships" },
      { label: relation.relation, href: path },
    ]);
    const evidence = page.getByRole("textbox", { name: "Evidence URL", exact: true });
    await evidence.waitFor();
    assert.equal(
      await evidence.inputValue(),
      `https://example.com/evidence/${index === 0 ? "forward" : "reverse"}`,
    );
    assert.equal(
      await page.getByRole("link", { name: "Cancel", exact: true }).getAttribute("href"),
      path,
    );
    assert.match(await page.title(), /Edit Depends on · Topic relationship/);
    const updated = `https://example.com/updated/${index}`;
    await evidence.fill(updated);
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await page.waitForURL(`${origin}${path}`);
    const update = writes.find(
      (write) => write.method === "PUT" && write.pathname === apiPath(relation),
    );
    assert.ok(update, "Edit targets the original directed relationship");
    assert.equal(update.body.evidence_url, updated);
    await page.reload();
    await page.getByRole("heading", { name: "Depends on", exact: true }).waitFor();
    await assertBreadcrumbs([
      { label: "Taxonomy", href: "/taxonomy" },
      { label: "Topic relationships", href: "/taxonomy/relationships" },
    ]);
    await page.getByRole("link", { name: "Delete", exact: true }).click();
    await page.waitForURL(`${origin}${path}/delete`);
    await page.getByRole("textbox", { name: "Type DELETE to confirm", exact: true }).fill("DELETE");
    await page.getByRole("button", { name: "Delete permanently", exact: true }).click();
    await page.waitForURL(`${origin}/taxonomy/relationships`);
    assert.ok(
      writes.some((write) => write.method === "DELETE" && write.pathname === apiPath(relation)),
    );
  }
  assert.equal(relationRecords.size, 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${origin}/jobs/analysis/topics/${sharedId}/logs`);
  await page.getByText("topic-analysis log", { exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(unexpected, []);
  assert.deepEqual(errors, []);
  console.log(
    "Admin browser checks passed: declared group cards and titles, nested clickable breadcrumbs, same-UUID pipelines, titles/tabs/logs/retries, directed relationship edits/deletes, and mobile layout.",
  );
} catch (error) {
  console.error(logs);
  console.error({ unexpected, writes });
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
}

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const userId = "11111111-1111-4111-8111-111111111111";
const user = {
  id: userId,
  name: "Ada",
  sign_in_name: "Ada Lovelace",
  email: "ada@example.test",
  avatar_url: null,
  created_at: "2026-09-01T10:00:00Z",
  last_seen_at: "2026-10-04T00:00:00Z",
  username: "ada",
  followed_topics: 3,
  followed_sources: 2,
  liked_articles: 4,
  bookmarks: 2,
  reads: 1,
  reading_days: 1,
  interests: 4,
  recommendations: 100,
  feed_status: "ready",
  computed_at: "2026-10-03T12:00:00Z",
  next_refresh_at: "2026-10-04T12:00:00Z",
  expires_at: "2026-10-04T18:00:00Z",
  refresh_attempts: 0,
  profile_bio: null,
  profile_location: null,
  profile_about: null,
  profile_public: false,
  profile_links: [],
  stack: [],
  dev_card: { theme: "editor", accent: "blue", motion: "static", stats: [] },
  dev_card_technologies: [],
  reading_streak: { current_days: 1, longest_days: 1, total_days: 1, last_read_date: "2026-10-04" },
  feed_preferences: { view: "cards", languages: ["en"], content_types: ["article"] },
  appearance_preferences: {
    theme: "system",
    timezone: "local",
    date_format: "locale",
    time_format: "system",
  },
  notification_preferences: { show_badge: true, sound: false },
};
let mode = "ready";
let rejectOnce = false;
const requests = [];
const fixture = createServer((req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  requests.push({ path, method: req.method });
  let body = {};
  if (path.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      name: "Admin",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (path.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path === `/v1/admin/users/${userId}`) body = user;
  else if (path === `/v1/admin/users/${userId}/interests`)
    body = { items: [], total: 0, limit: 5, offset: 0 };
  else if (path === `/v1/admin/users/${userId}/must-reads`) {
    if (rejectOnce) {
      rejectOnce = false;
      res.writeHead(503, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Unavailable" }));
      return;
    }
    body = {
      selection_date: "2026-10-04",
      timezone: "Asia/Kolkata",
      generated: mode !== "empty",
      presented_at: mode === "shown" ? "2026-10-04T00:00:00Z" : null,
      items:
        mode === "empty" || mode === "withdrawn"
          ? []
          : Array.from({ length: 5 }, (_, i) => ({
              id: `22222222-2222-4222-8222-22222222222${i}`,
              title: `Saved daily pick ${i + 1}`,
              position: i + 1,
              reason: "Because you follow Python",
              read: i === 0,
            })),
    };
  }
  res.writeHead(200, { "Content-Type": "application/json" });
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
app.stdout.on("data", (value) => {
  logs += value;
});
app.stderr.on("data", (value) => {
  logs += value;
});
let browser;
const output = `${root}/reports/admin-must-reads`;
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
    timezoneId: "America/New_York",
    reducedMotion: "reduce",
  });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/users/${userId}/analysis`);
  const selection = page.getByRole("region", { name: "Today’s Must Reads", exact: true });
  await selection.getByRole("link", { name: "Saved daily pick 1", exact: true }).waitFor();
  assert.equal(await selection.getByRole("link", { name: /^Saved daily pick/ }).count(), 5);
  assert.equal(await selection.getByText("1 of 5 read", { exact: true }).count(), 1);
  assert.equal(await selection.getByText("2026-10-04 · Asia/Kolkata", { exact: true }).count(), 1);
  assert.equal(await selection.getByText("Not shown yet", { exact: true }).count(), 1);
  assert.equal(
    await page.getByRole("region", { name: "Top recommendations", exact: true }).count(),
    0,
  );
  assert.equal(
    requests.some(({ path }) => path.endsWith("/recommendations")),
    false,
  );
  assert.equal(
    await selection
      .getByRole("link", { name: "Saved daily pick 1", exact: true })
      .getAttribute("href"),
    "/content/articles/22222222-2222-4222-8222-222222222220",
  );
  for (const theme of ["light", "dark"]) {
    await page
      .locator("html")
      .evaluate((node, theme) => node.classList.toggle("dark", theme === "dark"), theme);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await selection.scrollIntoViewIfNeeded();
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
        false,
      );
      await page.screenshot({ path: `${output}/${theme}-${width}.png`, fullPage: true });
    }
  }
  mode = "empty";
  await page.reload();
  await selection
    .getByText("No Must Reads have been generated for this user today.", { exact: true })
    .waitFor();
  mode = "withdrawn";
  await page.reload();
  await selection
    .getByText("No articles from today’s selection are currently available.", { exact: true })
    .waitFor();
  mode = "shown";
  rejectOnce = true;
  await page.reload();
  await selection.getByRole("button", { name: "Retry", exact: true }).click();
  await selection.getByRole("link", { name: "Saved daily pick 1", exact: true }).waitFor();
  await selection.getByText("Shown ·", { exact: true }).waitFor();
  assert.equal(
    requests.some(({ method }) => method !== "GET"),
    false,
  );
  assert.deepEqual(errors, []);
  console.log(
    "Admin Must Reads: saved picks, reasons, timezone, read progress, empty states, retry, and responsive themes passed.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.close();
}

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run after npm run admin:build. The API is a disposable local fixture.
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const requests = [];
const sourceId = "11111111-1111-4111-8111-111111111111";
const feed = "https://publication.example/feed";
const youtubeWatch = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLDeveloperVideos";
const youtubeChoices = [
  { target: "video", canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" },
  { target: "playlist", canonical_url: "https://www.youtube.com/playlist?list=PLDeveloperVideos" },
];
const fixture = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
  if (path.endsWith("/auth/me"))
    body = {
      subject: "fixture",
      issuer: "https://identity.example",
      organization_id: "fixture",
      roles: ["superuser"],
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      csrf_token: "fixture",
    };
  else if (path.endsWith("/settings"))
    body = { appearance: { theme: "light" }, defaults: { refresh_seconds: 0 } };
  else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path.endsWith("/sources/preview")) {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const input = JSON.parse(raw);
    requests.push(input);
    if (input.feed_url === youtubeWatch || input.feed_url === "@developers") {
      const kind = input.feed_url === "@developers" ? "channel" : (input.youtube_target ?? "video");
      body = {
        name:
          kind === "playlist"
            ? "Concurrency playlist"
            : kind === "channel"
              ? "Engineering channel"
              : "One developer video",
        provider: "youtube",
        resource_kind: kind,
        canonical_url:
          kind === "channel"
            ? "https://www.youtube.com/@developers"
            : youtubeChoices.find((item) => item.target === kind).canonical_url,
        resources:
          kind === "channel"
            ? [{ target: "channel", canonical_url: "https://www.youtube.com/@developers" }]
            : youtubeChoices,
        entries_seen: 1,
        entries_skipped: 0,
        warnings: [],
      };
    } else if (!input.use_solver) {
      res.statusCode = 422;
      body = {
        detail: { code: "browser_challenge", message: "Publisher requires browser verification." },
      };
    } else
      body = {
        name: "Engineering feed",
        description: "Engineering articles",
        website_url: null,
        logo_url: null,
        image_url: null,
        language: "en",
        entries_seen: 10,
        entries_skipped: 0,
        warnings: [],
      };
  }
  if (path.endsWith(`/sources/${sourceId}`))
    body = {
      id: sourceId,
      name: "Existing source",
      feed_url: feed,
      source_type: "publisher",
      enabled: false,
      poll_interval_seconds: 43200,
    };
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
try {
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${origin}/login`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: origin }]);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/content/sources/new`);
  await page.route("https://images.example.test/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="blue"/></svg>',
    }),
  );
  const logo = page.getByRole("textbox", { name: "Logo URL", exact: true });
  await logo.fill("https://images.example.test/first.svg");
  await page.getByRole("img", { name: "Logo preview", exact: true }).waitFor();
  await logo.fill("https://images.example.test/second.svg");
  assert.equal(await page.getByRole("img", { name: "Logo preview" }).count(), 0);
  await page.locator('img[alt="Logo preview"][src$="/second.svg"]').waitFor();
  await logo.fill("");
  assert.equal(await page.getByRole("img", { name: "Logo preview" }).count(), 0);
  await page.getByLabel("Feed or YouTube URL", { exact: false }).fill(feed);
  const retry = page.getByRole("button", { name: "Retry with solver" });
  await retry.waitFor();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].use_solver, undefined);
  await mkdir("/tmp/devfeed-issue120-admin", { recursive: true });
  await page.screenshot({
    path: "/tmp/devfeed-issue120-admin/explicit-retry.png",
    fullPage: true,
  });
  await retry.click();
  await page.getByLabel("Name", { exact: true }).evaluate(
    (input) =>
      new Promise((resolve) => {
        const check = () =>
          input.value === "Engineering feed" ? resolve() : setTimeout(check, 50);
        check();
      }),
  );
  assert.equal(requests[1].use_solver, true);
  await page.getByLabel("Feed or YouTube URL", { exact: false }).fill(feed + "-other");
  await retry.waitFor();
  assert.equal(requests[2].use_solver, undefined);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/devfeed-issue120-admin/mobile-retry.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.goto(`${origin}/content/sources/${sourceId}/edit`);
  const lookup = page.getByRole("button", { name: "Fetch source details" });
  await lookup.waitFor();
  const beforeEdit = requests.length;
  await lookup.click();
  await retry.waitFor();
  assert.equal(requests[beforeEdit].source_id, sourceId);
  assert.equal(requests[beforeEdit].use_solver, undefined);
  const solvedPreview = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/sources/preview") &&
      response.request().postDataJSON()?.source_id === sourceId &&
      response.request().postDataJSON()?.use_solver === true &&
      response.ok(),
  );
  await retry.click();
  await solvedPreview;
  await retry.waitFor({ state: "hidden" });
  assert.equal(requests.at(-1).use_solver, true);
  assert.equal(await page.getByRole("textbox", { name: /^Name/ }).inputValue(), "Existing source");
  await page.goto(`${origin}/content/sources/new`);
  await page.getByLabel("Feed or YouTube URL", { exact: false }).fill(youtubeWatch);
  await page.waitForFunction(
    () => document.querySelector('input[name="name"]')?.value === "One developer video",
  );
  const target = page.getByRole("combobox", { name: "YouTube target", exact: true });
  await target.waitFor();
  assert.equal(await target.textContent(), "Video");
  await target.click();
  await page.getByRole("option", { name: "Playlist", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector('input[name="name"]')?.value === "Concurrency playlist",
  );
  assert.equal(requests.at(-1).youtube_target, "playlist");
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: "/tmp/devfeed-issue120-admin/youtube-target.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({
    path: "/tmp/devfeed-issue120-admin/youtube-target-mobile.png",
    fullPage: true,
  });
  const handle = page.getByLabel("Feed or YouTube URL", { exact: false });
  await handle.fill("@developers");
  await page.waitForFunction(
    () => document.querySelector('input[name="name"]')?.value === "Engineering channel",
  );
  assert.equal(await handle.evaluate((input) => input.checkValidity()), true);
  assert.equal(requests.at(-1).feed_url, "@developers");
  assert.deepEqual(errors, []);
  console.log(
    "Source solver browser checks passed: explicit opt-in, filled metadata, reset, edit preservation, mobile layout.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

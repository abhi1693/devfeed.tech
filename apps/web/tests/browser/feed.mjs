import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const fixtureBundle = await build({
  entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(fixtureBundle.outputFiles[0].text).toString("base64")}`
);
let mode = "ready";
const fixture = createServer((req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  const authenticated = req.headers.cookie?.includes("devfeed_user_session=valid");
  let body = {};
  if (path === "/v1/user/auth/me")
    body = authenticated
      ? {
          user_id: "user",
          name: "Reader",
          email: "reader@example.test",
          csrf_token: "test",
          expires_at: Date.now() / 1000 + 3600,
        }
      : null;
  else if (path === "/v1/user/auth/config") body = { enabled: true };
  else if (path === "/v1/user/settings/profile")
    body = { display_name: "Reader", avatar_url: null };
  else if (path === "/v1/user/settings/appearance") body = { theme: "light" };
  else if (path === "/v1/user/settings/feed")
    body = {
      view: "cards",
      content_types: ["news", "article", "tutorial", "release", "comparison", "opinion", "other"],
    };
  else if (path === "/v1/feed/options")
    body = { sources: [source], content_types: ["article"], languages: ["en"] };
  else if (path === "/v1/topics") body = [topic];
  else if (path === "/v1/user/engagement") body = [];
  else if (path === "/v1/user/preferences") body = { topic_ids: [] };
  else if (path === "/v1/user/source-preferences") body = { source_ids: [] };
  else if (path === "/v1/user/feed")
    body = {
      status: mode === "refreshing" ? "refreshing" : "ready",
      generation: mode === "new" ? "new" : "old",
      has_interests: true,
      items: [
        { ...article, title: mode === "new" ? "New recommendation" : "Previous recommendation" },
      ],
      next_cursor: null,
      reasons: {},
    };
  else if (path === "/v1/feed") body = { items: [article], next_cursor: null };
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const upstream = `http://127.0.0.1:${fixture.address().port}`;
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
    cwd: `${root}/apps/web`,
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
    },
    stdio: "pipe",
  },
);
let logs = "",
  browser;
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
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
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await page.goto(origin);
  await page.waitForURL(`${origin}/latest`);
  assert.equal(await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(), 0);
  assert.equal(
    await page.locator(".mobile-nav").getByRole("link", { name: "Read later" }).count(),
    0,
  );
  await context.addCookies([{ name: "devfeed_user_session", value: "expired", url: origin }]);
  await page.goto(origin);
  await page.waitForURL(`${origin}/latest`);
  await context.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
  await page.goto(origin);
  await page.getByRole("heading", { name: "My feed", exact: true }).waitFor();
  await page.getByRole("link", { name: "Previous recommendation", exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/");
  assert.equal(await page.locator(".sidebar").getByRole("link", { name: "Read later" }).count(), 1);
  mode = "refreshing";
  await page.evaluate(() => window.dispatchEvent(new Event("devfeed:interests-changed")));
  await page.getByText("Updating recommendations in the background…").waitFor();
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    1,
  );
  assert.equal(await page.getByRole("heading", { name: "Updating your feed" }).count(), 0);
  const output = `${root}/reports/reader-feed`;
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/background-refresh.png`, fullPage: true });
  mode = "new";
  await page
    .getByRole("link", { name: "New recommendation", exact: true })
    .waitFor({ timeout: 15000 });
  assert.equal(
    await page.getByRole("link", { name: "Previous recommendation", exact: true }).count(),
    0,
  );
  await page.locator(".sidebar").getByRole("link", { name: "Latest feed", exact: true }).click();
  await page.waitForURL(`${origin}/latest`);
  await page.goto(`${origin}/my-feed`);
  await page.waitForURL(`${origin}/`);
  console.log(
    "Reader routes, signed-out navigation, and background recommendation refresh passed.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  fixture.close();
}

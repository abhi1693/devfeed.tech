import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
let saved;
const profile = {
  display_name: "Reader",
  avatar_url: null,
  username: "reader",
  bio: "Bio",
  reading_streak: { current_days: 3 },
  stack: [{ topic_id: "one", name: "Python" }],
};
const upstream = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = {};
  if (path.endsWith("/auth/me"))
    body = {
      user_id: "one",
      name: "Reader",
      email: "reader@example.test",
      csrf_token: "test",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    };
  else if (path.endsWith("/settings/profile")) {
    if (req.method === "PUT") {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      saved = JSON.parse(Buffer.concat(chunks).toString());
      profile.display_name = saved.display_name;
    }
    body = profile;
  } else if (path.endsWith("/settings/appearance")) body = { theme: "light" };
  else if (path.endsWith("/settings/feed")) body = { languages: ["en"] };
  else if (path.endsWith("/notifications/config")) body = { enabled: false };
  else if (path.endsWith("/auth/config")) body = { enabled: true };
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
});
await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const api = `http://127.0.0.1:${upstream.address().port}`;
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
      DEVFEED_PUBLIC_API_URL: api,
      DEVFEED_USER_API_URL: api,
      DEVFEED_USER_BASE_URL: origin,
    },
    stdio: "pipe",
  },
);
let browser;
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
try {
  for (let i = 0; i < 100; i++) {
    try {
      await fetch(`${origin}/login`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto(`${origin}/settings/profile`);
  await page.getByLabel("Display name").fill("Updated Reader");
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Your profile is saved.").waitFor();
  assert.deepEqual(saved, { display_name: "Updated Reader", avatar_url: null });
  assert.equal(profile.reading_streak.current_days, 3);
  console.log("Web profile editing preserves computed profile fields.");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => upstream.close(resolve));
}

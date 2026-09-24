import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { checkProfileEditor } from "../../../../scripts/testing/profile-editor.mjs";
import { checkDevCard } from "../../../../scripts/testing/dev-card.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const avatarFixture = await readFile(`${root}/packages/theme/assets/devfeed-mark.png`);
let saved;
const profile = {
  display_name: "Reader",
  avatar_url: null,
  username: "reader",
  bio: "",
  reading_streak: { current_days: 3 },
  stack: [],
};
const upstream = createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  if (path === "/avatar.png") {
    res.writeHead(200, {
      "Content-Type": "image/png",
      ...(req.url.includes("cors=yes") ? { "Access-Control-Allow-Origin": "*" } : {}),
    });
    res.end(avatarFixture);
    return;
  }
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
  } else if (path === "/v1/topics")
    body = [{ id: "rust", name: "Rust", slug: "rust", kind: "technology", logo_url: null }];
  else if (path.endsWith("/settings/appearance")) body = { theme: "light" };
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await page.addInitScript(() => {
    if (!sessionStorage.getItem("draft-test-seeded")) {
      sessionStorage.setItem(
        "devfeed:dev-card-draft",
        JSON.stringify({ name: "Maya Preview", stack: [], ready: true, created: Date.now() }),
      );
      sessionStorage.setItem("draft-test-seeded", "true");
    }
  });
  await page.goto(`${origin}/settings/profile`);
  await page
    .getByText("Your dev card preview is ready. Review your details and save changes to keep it.")
    .waitFor();
  assert.equal(await page.getByLabel("Display name").inputValue(), "Maya Preview");
  await page.getByLabel("Display name").fill("Updated Reader");
  await page
    .getByRole("button", { name: "Save changes" })
    .locator("svg.lucide-save")
    .waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Your profile is saved.").waitFor();
  assert.equal(saved.display_name, "Updated Reader");
  assert.equal(await page.evaluate(() => sessionStorage.getItem("devfeed:dev-card-draft")), null);
  assert.equal(saved.reading_streak, undefined);
  assert.equal(profile.reading_streak.current_days, 3);
  await page.evaluate(() => {
    document.activeElement?.blur();
    scrollTo(0, 0);
  });
  await page.screenshot({ path: "/tmp/profile-desktop.png", fullPage: true });
  await checkProfileEditor(page, "/tmp/profile-direct");
  await checkDevCard(page, "/tmp/dev-card-web");
  // A filled-out fixture exercises the public fields and the populated card design.
  Object.assign(profile, {
    display_name: "Maya Chen",
    username: "mayacodes",
    bio: "I build tools for developers, contribute to open source, and enjoy exploring distributed systems. Currently learning Rust and sharing everything I learn as I go.".slice(
      0,
      160,
    ),
    location: "Berlin",
    visibility: { public: true, stack: true, heatmap: true, location: true, achievements: false },
    reading_streak: {
      current_days: 8,
      longest_days: 24,
      total_days: 128,
      last_read_date: "2026-09-22",
    },
    stack: ["AI Bots", ".NET", "Kubernetes", "Rust"].map((name) => ({
      topic_id: name.toLowerCase(),
      name,
      slug: name.toLowerCase(),
      section: "primary",
      since_year: null,
      logo_url: null,
      status: "active",
    })),
  });
  await page.reload();
  await page.getByRole("button", { name: "User menu: Maya Chen", exact: true }).waitFor();
  await checkDevCard(page, "/tmp/dev-card-filled");
  // Very long names and bios must stay inside the card, including in the PNG.
  await page.getByLabel("Display name").fill("W".repeat(100));
  await page.getByLabel("Short bio").fill("界".repeat(160));
  assert.equal(
    await page.locator(".dev-card-preview svg text").evaluateAll((nodes) =>
      nodes.every((node) => {
        const box = node.getBBox();
        return box.x >= 0 && box.x + box.width <= 560;
      }),
    ),
    true,
  );
  await page.getByRole("button", { name: "Discard changes", exact: true }).click();
  for (const allowed of [true, false]) {
    profile.avatar_url = `${api}/avatar.png?cors=${allowed ? "yes" : "no"}`;
    await page.reload();
    await page.getByRole("button", { name: "User menu: Maya Chen", exact: true }).click();
    await page.getByRole("menuitem", { name: "Dev card", exact: true }).click();
    const card = page.getByRole("complementary", { name: "Dev card preview", exact: true });
    await card.locator("svg image[data-avatar]").waitFor();
    const download = page.waitForEvent("download");
    await card.getByRole("button", { name: "Download card", exact: true }).click();
    assert.equal(await (await download).failure(), null);
    await card
      .getByText(
        allowed
          ? "Your card is downloaded."
          : "Your card is downloaded. Used initials because your photo host doesn’t allow image export.",
        { exact: true },
      )
      .waitFor();
  }
  profile.avatar_url = null;
  await page.reload();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => {
    document.activeElement?.blur();
    scrollTo(0, 0);
  });
  await page.screenshot({ path: "/tmp/profile-mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  console.log("Web profile editing preserves computed profile fields.");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => upstream.close(resolve));
}

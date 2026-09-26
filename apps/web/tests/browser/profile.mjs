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
let publicAvailable = true;
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
  if (path.startsWith("/v1/user/profiles/")) {
    assert.equal(req.headers.cookie, undefined, "public profile fetches carry no session");
    if (publicAvailable && path === "/v1/user/profiles/reader/reading-heatmap") {
      const start = new Date("2026-01-01T00:00:00Z");
      const days = Array.from({ length: 365 }, (_, index) => ({
        date: new Date(start.getTime() + index * 86400000).toISOString().slice(0, 10),
        article_count: index % 7 === 0 ? 4 : index % 5 === 0 ? 2 : 0,
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ year: 2026, timezone: "UTC", days }));
      return;
    }
    if (!publicAvailable || path !== "/v1/user/profiles/reader") {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Profile not found" }));
      return;
    }
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(
      JSON.stringify({
        username: "reader",
        display_name: "Public Reader",
        avatar_url: null,
        bio: "Building useful things.",
        about:
          "I build developer tools that make everyday work simpler. Currently exploring better ways to learn in public.\n\nOutside of code: good coffee, long walks, and a growing reading list.",
        location: "Bengaluru, India",
        links: [
          { url: "https://github.com/reader", label: "GitHub" },
          { url: "https://example.com", label: "Website" },
        ],
        stack: [
          {
            topic_id: "typescript",
            name: "TypeScript",
            slug: "typescript",
            kind: "language",
            section: "primary",
            since_year: 2020,
            logo_url: `${api}/avatar.png?cors=yes`,
          },
          {
            topic_id: "python",
            name: "Python",
            slug: "python",
            kind: "language",
            section: "primary",
            since_year: 2018,
          },
          {
            topic_id: "rust",
            name: "Rust",
            slug: "rust",
            kind: "language",
            section: "learning",
          },
        ],
        reading_streak: { current_days: 7, longest_days: 12, total_days: 30 },
      }),
    );
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
    body = [
      {
        id: "rust",
        name: "Rust",
        slug: "rust",
        kind: "technology",
        logo_url: `${api}/avatar.png?cors=yes`,
      },
    ];
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
      kind: name === ".NET" ? "framework" : "tool",
      section: "primary",
      since_year: null,
      logo_url:
        name === "Kubernetes"
          ? `${api}/avatar.png?cors=yes`
          : name === ".NET"
            ? `${api}/missing-logo.svg`
            : null,
      status: "active",
    })),
  });
  await page.reload();
  await page.getByRole("button", { name: "User menu: Maya Chen", exact: true }).waitFor();
  await page.locator('.dev-card-preview svg image[data-technology-logo="kubernetes"]').waitFor();
  await page
    .locator('.dev-card-preview svg text[data-technology-fallback=".net"][visibility="visible"]')
    .waitFor();
  await checkDevCard(page, "/tmp/dev-card-filled");
  // Very long names and bios must stay inside the card, including in the PNG.
  await page.getByLabel("Display name").fill("W".repeat(100));
  await page.getByLabel("Short bio").fill("界".repeat(160));
  assert.equal(
    await page.locator(".dev-card-preview svg text").evaluateAll((nodes) =>
      nodes
        .filter((node) => node.getAttribute("visibility") !== "hidden")
        .every((node) => {
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
    await page.getByRole("menuitem", { name: "Profile settings", exact: true }).click();
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
  await page.context().route("**/api/v1/user/auth/me", (route) => route.fulfill({ json: null }));
  await page.addInitScript(() => {
    window.addEventListener("devfeed:analytics", (event) => {
      if (!event.detail.name.startsWith("dev_card_")) return;
      const events = JSON.parse(sessionStorage.getItem("card-test-events") ?? "[]");
      sessionStorage.setItem("card-test-events", JSON.stringify([...events, event.detail]));
    });
  });
  const response = await page.goto(`${origin}/users/reader`);
  assert.equal(response.status(), 200);
  assert.match(response.headers()["cache-control"], /no-store/);
  assert.equal(await page.getByRole("img", { name: /Dev card for/ }).count(), 0);
  await page.getByRole("heading", { name: "Public Reader", exact: true }).waitFor();
  assert.equal(await page.getByText("DevFeed reader", { exact: true }).count(), 0);
  assert.equal(await page.locator(".public-profile .lucide-arrow-up-right").count(), 0);
  await page.getByRole("heading", { name: "Stack & technologies", exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("link", { name: /TypeScript/ })
      .locator("img")
      .getAttribute("src"),
    `${api}/avatar.png?cors=yes`,
  );
  assert.equal(await page.locator(".public-profile-days .public-profile-day").count(), 365);
  await page.getByRole("link", { name: "GitHub", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Edit profile", exact: true }).count(), 0);
  const embed = await fetch(`${origin}/api/v1/users/reader/card.svg`);
  assert.equal(embed.status, 200);
  assert.match(embed.headers.get("content-type"), /^image\/svg\+xml/);
  assert.match(embed.headers.get("cache-control"), /no-store/);
  const svg = await embed.text();
  assert.ok(svg.startsWith("<svg"));
  assert.ok(svg.includes("data-brand-mark") && svg.includes("data:image/png;base64,"));
  assert.ok(svg.includes("Public Reader"));
  assert.ok(svg.includes(`${api}/avatar.png?cors=yes`));
  assert.equal(svg.includes("<html"), false);
  assert.equal(svg.includes("var(--"), false);
  const imagePage = await browser.newPage({ viewport: { width: 600, height: 900 } });
  await imagePage.goto(`${origin}/api/v1/users/reader/card.svg`);
  await imagePage.screenshot({ path: "/tmp/devfeed-embedded-card.png" });
  await imagePage.close();
  await page.bringToFront();
  assert.equal(
    await page.locator('meta[property="og:image"]').getAttribute("content"),
    `${origin}/users/reader/image`,
  );
  const socialImage = await fetch(`${origin}/users/reader/image`);
  assert.equal(socialImage.status, 200);
  assert.match(socialImage.headers.get("cache-control"), /no-store/);
  const png = Buffer.from(await socialImage.arrayBuffer());
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png.readUInt32BE(16), 1200);
  assert.equal(png.readUInt32BE(20), 630);
  await import("node:fs/promises").then(({ writeFile }) =>
    writeFile("/tmp/devfeed-social-card.png", png),
  );
  for (const icon of await page.locator(".public-profile-technology .topic-icon").all()) {
    const box = await icon.boundingBox();
    assert.equal(box.width, 32);
    assert.equal(box.height, 32);
  }
  await page.screenshot({ path: "/tmp/devfeed-public-card-mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "/tmp/devfeed-public-card-desktop.png", fullPage: true });
  await page.locator("html").evaluate((node) => node.classList.add("dark"));
  await page.waitForTimeout(350);
  await page.screenshot({ path: "/tmp/devfeed-public-profile-dark.png", fullPage: true });
  await page.locator("html").evaluate((node) => node.classList.remove("dark"));
  assert.equal(await page.getByLabel("Markdown embed code").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Copy Link", exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "Create yours", exact: true }).count(), 0);
  await page.goto(`${origin}/dev-card`);
  await page.getByRole("button", { name: "Preview your card", exact: true }).click();
  const reveal = page.getByRole("dialog", { name: "Your dev card preview" });
  await reveal.waitFor();
  await reveal.getByRole("button", { name: "Create your dev card", exact: true }).click();
  await reveal.getByLabel("Your display name").fill("New Card Reader");
  const signup = reveal.getByRole("link", { name: "Save my dev card", exact: true });
  const signupUrl = new URL(await signup.getAttribute("href"), origin);
  assert.equal(signupUrl.searchParams.has("register"), false);
  assert.equal(signupUrl.searchParams.get("return_to"), "/settings/profile");
  await signup.evaluate((node) =>
    node.addEventListener("click", (event) => event.preventDefault(), { once: true }),
  );
  await signup.click();
  assert.equal(
    await page.evaluate(() => JSON.parse(sessionStorage.getItem("devfeed:dev-card-draft")).name),
    "New Card Reader",
  );
  const events = await page.evaluate(() => JSON.parse(sessionStorage.getItem("card-test-events")));
  for (const name of ["dev_card_view", "dev_card_preview_started", "dev_card_signup_started"]) {
    assert.ok(
      events.some((event) => event.name === name),
      `tracks ${name}`,
    );
  }
  assert.ok(
    events.every((event) => Object.keys(event.params).every((key) => key === "method")),
    "funnel events contain no personal fields",
  );
  publicAvailable = false;
  assert.equal((await fetch(`${origin}/users/reader`)).status, 404);
  assert.equal((await fetch(`${origin}/users/reader/image`)).status, 404);
  assert.equal((await fetch(`${origin}/api/v1/users/reader/card.svg`)).status, 404);
  assert.equal((await fetch(`${origin}/users/missing-reader`)).status, 404);
  console.log("Profile editing, public card sharing, social images, and guest creation passed.");
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => upstream.close(resolve));
}

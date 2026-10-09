import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { appendFile, cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { lighthouseConfig, readerPaths, runs } from "./browser-performance-config.mjs";
import { checkMeasurements, measurementSummary } from "./browser-performance-report.mjs";
import { applyArticleSnapshot } from "./browser-performance-fixture.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const articleEntryPhase = process.argv
  .find((arg) => arg.startsWith("--article-entry="))
  ?.split("=")[1];
if (articleEntryPhase && !["before", "after"].includes(articleEntryPhase))
  throw new Error("Article entry phase must be before or after");
const measurementRuns = articleEntryPhase ? 5 : runs;
const output = articleEntryPhase
  ? `${root}/reports/article-entry/${articleEntryPhase}`
  : `${root}/reports/browser-performance`;
const imageDirectory = `${root}/apps/web/public/_browser-budgets`;
const sharp = createRequire(`${root}/apps/web/package.json`)("sharp");
// Keep local credentials and production telemetry out of fixture builds and servers.
const env = {
  ...Object.fromEntries(
    Object.entries(process.env).filter(
      ([key]) =>
        key !== "NODE_V8_COVERAGE" &&
        !/^(DEVFEED_|GOOGLE_ANALYTICS_|MICROSOFT_CLARITY_|FARO_|OTEL_)/.test(key),
    ),
  ),
  NEXT_TELEMETRY_DISABLED: "1",
  DEVFEED_ANALYTICS_ENABLED: "false",
  DEVFEED_FARO_ENABLED: "false",
  DEVFEED_METRICS_ENABLED: "false",
  DEVFEED_X_PIXEL_ENABLED: "false",
};
const lhci = `${root}/node_modules/@lhci/cli/src/cli.js`;

async function run(command, args, cwd = root) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit" });
  const [code] = await once(child, "exit");
  assert.equal(code, 0, `${command} ${args.join(" ")} failed (${code})`);
}

async function loadFixture(path) {
  const result = await build({ entryPoints: [path], bundle: true, write: false, format: "esm" });
  return import(
    `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`
  );
}

async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
}

async function freePort() {
  const probe = createServer();
  const origin = await listen(probe);
  await new Promise((resolve) => probe.close(resolve));
  return origin;
}

function fixtureServer(fixture, origin) {
  const unknown = new Set();
  const topics = Array.from({ length: 60 }, (_, index) => ({
    ...fixture.topic,
    id: `topic-${index}`,
    slug: index ? `budget-topic-${index}` : fixture.topic.slug,
    name: index ? `Engineering topic ${index}` : fixture.topic.name,
    logo_url: `${origin}/_browser-budgets/icon.webp?topic=${index}`,
  }));
  const sources = Array.from({ length: 60 }, (_, index) => ({
    ...fixture.source,
    id: `11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}`,
    slug: `budget-source-${index}`,
    name: `Engineering publisher ${index}`,
    description: "Developer news, tutorials and engineering practice.",
    logo_url: `${origin}/_browser-budgets/icon.webp?source=${index}`,
  }));
  const items = Array.from({ length: 24 }, (_, index) => ({
    ...fixture.article,
    id: `22222222-2222-4222-8222-${String(index + 1).padStart(12, "0")}`,
    slug: index ? `browser-budget-article-${index}` : fixture.article.slug,
    title: index ? `TypeScript engineering article ${index}` : fixture.article.title,
    image_url: `${origin}/_browser-budgets/cover.webp?article=${index}`,
  }));
  const profile = {
    username: "budget-reader",
    display_name: "Budget fixture reader",
    avatar_url: `${origin}/_browser-budgets/icon.webp?avatar=profile`,
    bio: "Building better reader experiences.",
    location: "Bengaluru, India",
    about: "An engineer sharing practical lessons and learning through daily reading.",
    links: [{ url: "https://github.com/example", label: "GitHub" }],
    stack: topics.slice(0, 12).map((topic, index) => ({
      ...topic,
      topic_id: topic.id,
      section: index < 6 ? "primary" : "learning",
      since_year: 2020 + (index % 5),
    })),
    reading_streak: { current_days: 3, longest_days: 19, total_days: 42 },
  };
  const ranks = Array.from({ length: 10 }, (_, index) => ({
    rank: index + 1,
    username: index ? `budget-reader-${index}` : profile.username,
    display_name: index ? `Reader ${index}` : profile.display_name,
    avatar_url: `${origin}/_browser-budgets/icon.webp?avatar=${index}`,
    days: 100 - index,
  }));
  const feed = { items, next_cursor: null };
  const bodies = new Map([
    ["/v1/feed", feed],
    ["/v1/user/trending", feed],
    ...items.map((item) => [`/v1/articles/${item.slug}`, item]),
    ["/v1/feed/options", { sources, content_types: ["tutorial"], languages: ["en"] }],
    ["/v1/topics", topics],
    ...topics.map((topic) => [`/v1/topics/${topic.slug}`, topic]),
    ["/v1/sources", sources],
    ...sources.map((source) => [`/v1/sources/${source.slug}`, source]),
    ["/v1/user/auth/me", null],
    ["/v1/user/auth/config", { enabled: true, providers: [] }],
    [
      "/v1/user/engagement",
      items.map((item) => ({ article_id: item.id, opens: 5, likes: 2, liked: false })),
    ],
    ["/v1/user/profiles/budget-reader", profile],
    ["/v1/user/profiles/asaharan", { ...profile, username: "asaharan" }],
    [
      "/v1/user/profiles/budget-reader/reading-heatmap",
      {
        year: 2026,
        timezone: "UTC",
        days: Array.from({ length: 365 }, (_, index) => ({
          date: new Date(Date.UTC(2026, 0, 1) + index * 86400000).toISOString().slice(0, 10),
          article_count: index % 7 === 0 ? 4 : index % 5 === 0 ? 2 : 0,
        })),
      },
    ],
    [
      "/v1/user/leaderboard",
      {
        longest_streak: ranks,
        reading_days: ranks.map((rank) => ({ ...rank, days: rank.days + 50 })),
      },
    ],
  ]);
  const server = createServer((req, res) => {
    const path = new URL(req.url, origin).pathname;
    let body = bodies.get(path);
    if (body === undefined) {
      unknown.add(path);
      res.statusCode = 404;
      body = { detail: "Unconfigured browser budget fixture" };
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(body));
  });
  return { server, unknown };
}

async function waitForReady(next, origin) {
  for (let attempt = 0; attempt < 100; attempt++) {
    assert.equal(next.exitCode, null, "Production server exited before becoming ready");
    try {
      if ((await fetch(`${origin}/login`, { signal: AbortSignal.timeout(1000) })).ok) return;
    } catch {
      /* Wait for the production server to bind. */
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("Production server did not become ready");
}

async function checkPageContent(urls) {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 412, height: 823 },
    });
    for (const url of urls) {
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      assert.equal((await page.goto(url)).status(), 200);
      assert.equal(page.url(), url);
      const pathname = new URL(url).pathname;
      if (pathname === "/topics" || pathname === "/sources") {
        await page.locator(".catalog-card").first().waitFor();
        assert.equal(await page.locator(".catalog-card").count(), 60);
      } else if (pathname === "/leaderboard") {
        for (const name of ["Longest streak", "Most reading days"]) {
          const board = page.getByRole("region", { name, exact: true });
          await board.getByRole("listitem").first().waitFor();
          assert.equal(await board.getByRole("listitem").count(), 10);
        }
      } else if (pathname.startsWith("/users/")) {
        await page.getByRole("heading", { name: "Budget fixture reader", exact: true }).waitFor();
        assert.equal(await page.locator(".public-profile-day[title]").count(), 365);
        assert.equal(await page.locator(".public-profile-technology").count(), 12);
      } else if (pathname.startsWith("/articles/")) {
        await page.locator("#article-preview-title").waitFor();
        await page.locator(".preview-read-button").waitFor();
        const cover = page.locator(".preview-cover img");
        assert.equal(await cover.getAttribute("loading"), "eager");
        assert.equal(await cover.getAttribute("fetchpriority"), "high");
        assert.ok(
          await page
            .locator(".article-grid .card-image img")
            .evaluateAll((images) =>
              images.every(
                (image, index) =>
                  image.loading === (index === 0 ? "eager" : "lazy") &&
                  image.fetchPriority === (index === 0 ? "high" : "auto"),
              ),
            ),
          "Only the first visible backdrop cover should have priority",
        );
      } else {
        await page.locator(".article-card").first().waitFor();
        assert.equal(await page.locator(".article-card").count(), 24);
      }
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll("main img")].filter((image) => {
          const box = image.getBoundingClientRect();
          return box.top < innerHeight && box.bottom > 0;
        });
        return (
          images.length > 0 && images.every((image) => image.complete && image.naturalWidth > 0)
        );
      });
      assert.deepEqual(errors, [], `Browser errors on ${url}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

async function audit() {
  const destination = `${output}/web`;
  await mkdir(destination, { recursive: true });
  await mkdir(imageDirectory, { recursive: true });
  await sharp(`${root}/apps/web/public/opengraph.png`)
    .resize({ width: 640 })
    .webp({ quality: 75 })
    .toFile(`${imageDirectory}/cover.webp`);
  await sharp(`${root}/apps/web/public/opengraph.png`)
    .resize({ width: 128, height: 128, fit: "cover" })
    .webp({ quality: 75 })
    .toFile(`${imageDirectory}/icon.webp`);
  const origin = await freePort();
  const fixture = await loadFixture(`${root}/apps/web/tests/fixtures.ts`);
  const snapshotPath = process.argv
    .find((arg) => arg.startsWith("--article-fixture="))
    ?.slice("--article-fixture=".length);
  if (snapshotPath) {
    const snapshot = JSON.parse(await readFile(snapshotPath, "utf8"));
    applyArticleSnapshot(fixture, snapshot, articleEntryPhase);
    await writeFile(`${output}/article-fixture.json`, JSON.stringify(snapshot, null, 2));
  }
  const { server, unknown } = fixtureServer(fixture, origin);
  const upstream = await listen(server);
  const urls = readerPaths(fixture.article.slug)
    .filter((path) => !articleEntryPhase || path.startsWith("/articles/"))
    .map((path) => origin + path);
  const appEnv = {
    DEVFEED_PUBLIC_API_URL: upstream,
    DEVFEED_USER_API_URL: upstream,
    DEVFEED_USER_BASE_URL: origin,
  };
  const next = spawn(
    process.execPath,
    [
      "../../node_modules/next/dist/bin/next",
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      new URL(origin).port,
    ],
    { cwd: `${root}/apps/web`, env: { ...env, ...appEnv }, stdio: ["ignore", "pipe", "pipe"] },
  );
  let logs = "";
  next.stdout.on("data", (chunk) => (logs += chunk));
  next.stderr.on("data", (chunk) => (logs += chunk));
  try {
    await waitForReady(next, origin);
    await checkPageContent(urls);
    assert.deepEqual([...unknown], [], "Some API fixtures are missing");
    const config = lighthouseConfig(urls, chromium.executablePath(), `${destination}/html`);
    config.ci.collect.numberOfRuns = measurementRuns;
    const configPath = `${destination}/config.json`;
    await writeFile(configPath, JSON.stringify(config, null, 2));
    await run(process.execPath, [lhci, "collect", `--config=${configPath}`], destination);
    const raw = `${destination}/.lighthouseci`;
    const reports = await Promise.all(
      (await readdir(raw))
        .filter((name) => /^lhr-.*\.json$/.test(name))
        .map(async (name) => JSON.parse(await readFile(`${raw}/${name}`, "utf8"))),
    );
    checkMeasurements(reports, urls, measurementRuns);
    // Export the HTML even when an assertion fails, so the failure is reviewable.
    await run(process.execPath, [lhci, "upload", `--config=${configPath}`], destination);
    let failed;
    try {
      await run(process.execPath, [lhci, "assert", `--config=${configPath}`], destination);
    } catch (error) {
      failed = error;
    }
    const assertions = JSON.parse(await readFile(`${raw}/assertion-results.json`, "utf8"));
    const expectedAssertions = urls.reduce(
      (count, url) =>
        count +
        config.ci.assert.assertMatrix.reduce(
          (pageCount, row) =>
            pageCount +
            (new RegExp(row.matchingUrlPattern).test(url) ? Object.keys(row.assertions).length : 0),
          0,
        ),
      0,
    );
    assert.equal(assertions.length, expectedAssertions);
    const summary = measurementSummary(reports, urls, assertions);
    await appendFile(`${output}/summary.md`, summary);
    if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, summary);
    assert.deepEqual([...unknown], [], "Some API fixtures are missing");
    if (failed) throw failed;
  } finally {
    await writeFile(`${destination}/server.log`, logs);
    const raw = `${destination}/.lighthouseci`;
    await cp(raw, `${destination}/raw`, { recursive: true }).catch((error) => {
      if (error.code !== "ENOENT") throw error;
    });
    if (next.exitCode === null) {
      next.kill("SIGTERM");
      await once(next, "exit");
    }
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await writeFile(`${output}/summary.md`, "# Browser budgets\n\n");
let failed = false;
try {
  await run("npm", ["run", "web:build"]);
  await audit();
} catch (error) {
  failed = true;
  console.error(error);
  const message = `\nReader browser budgets failed: ${error.message}\n`;
  await appendFile(`${output}/summary.md`, message);
  if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, message);
} finally {
  await rm(imageDirectory, { recursive: true, force: true });
}
process.exitCode = failed ? 1 : 0;

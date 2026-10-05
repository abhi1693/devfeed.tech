import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { appendFile, cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import { lighthouseConfig, runs } from "./browser-performance-config.mjs";
import { checkMeasurements, measurementSummary } from "./browser-performance-report.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = `${root}/reports/browser-performance`;
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

function fixtureServer(app, fixture, origin) {
  const unknown = new Set();
  const items =
    app === "web"
      ? Array.from({ length: 24 }, (_, index) => ({
          ...fixture.article,
          id: `22222222-2222-4222-8222-${String(index + 1).padStart(12, "0")}`,
          slug: index ? `browser-budget-article-${index}` : fixture.article.slug,
          title: index ? `TypeScript engineering article ${index}` : fixture.article.title,
          // Use a normal thumbnail, not the full-resolution social sharing image.
          image_url: `${origin}/_browser-budgets/cover.webp?article=${index}`,
        }))
      : [];
  const feed = { items, next_cursor: null };
  const bodies = new Map(
    app === "web"
      ? [
          ["/v1/feed", feed],
          ["/v1/user/trending", feed],
          ...items.map((item) => [`/v1/articles/${item.slug}`, item]),
          [
            "/v1/feed/options",
            { sources: [fixture.source], content_types: ["tutorial"], languages: ["en"] },
          ],
          ["/v1/topics", [fixture.topic]],
          [`/v1/topics/${fixture.topic.slug}`, fixture.topic],
          ["/v1/sources", [fixture.source]],
          ["/v1/user/auth/me", null],
          ["/v1/user/auth/config", { enabled: true, providers: [] }],
          [
            "/v1/user/engagement",
            items.map((item) => ({ article_id: item.id, opens: 5, likes: 2, liked: false })),
          ],
          [
            "/v1/user/profiles/asaharan",
            {
              username: "asaharan",
              display_name: "Budget fixture reader",
              avatar_url: null,
              bio: "Building better reader experiences.",
              stack: [],
              reading_streak: { current_days: 3, longest_days: 19, total_days: 42 },
            },
          ],
        ]
      : [
          [
            "/v1/admin/auth/me",
            {
              subject: "fixture",
              name: "Budget reviewer",
              roles: ["superuser"],
              issuer: "https://identity.example",
              organization_id: "fixture",
              expires_at: Math.floor(Date.now() / 1000) + 3600,
              csrf_token: "fixture",
            },
          ],
          ["/v1/admin/auth/config", { enabled: true, providers: [] }],
          [
            "/v1/admin/settings",
            { appearance: { theme: "dark" }, defaults: { refresh_seconds: 0, overview_days: 30 } },
          ],
          ["/v1/admin/notifications/config", { enabled: false }],
          [
            "/v1/admin/ai/connection",
            { state: "disconnected", message: "Not connected", quota: [] },
          ],
        ],
  );
  const server = createServer((req, res) => {
    const path = new URL(req.url, origin).pathname;
    let body = bodies.get(path);
    if (app === "admin" && path.startsWith("/v1/admin/overview/panels/"))
      body = { ...fixture.populatedOverview, generated_at: new Date().toISOString() };
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

async function checkPageContent(app, urls) {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: app === "web" ? { width: 412, height: 823 } : { width: 1350, height: 940 },
    });
    if (app === "admin")
      await context.addCookies([{ name: "devfeed_admin_session", value: "fixture", url: urls[0] }]);
    for (const url of urls) {
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      assert.equal((await page.goto(url)).status(), 200);
      assert.equal(page.url(), url);
      if (app === "admin") {
        await page
          .locator('[data-overview-panel="publications"] .overview-panel-content')
          .waitFor();
        assert.ok((await page.locator("[data-overview-panel]").count()) > 0);
      } else if (new URL(url).pathname.startsWith("/articles/")) {
        await page.locator("#article-preview-title").waitFor();
        await page.getByRole("link", { name: "Read tutorial", exact: true }).waitFor();
      } else {
        await page.locator(".article-card").first().waitFor();
        assert.equal(await page.locator(".article-card").count(), 24);
      }
      if (app === "web")
        await page.waitForFunction(() => {
          const image = document.querySelector(".article-card .card-image img");
          return image?.complete && image.naturalWidth > 0;
        });
      assert.deepEqual(errors, [], `Browser errors on ${url}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

async function audit(app) {
  const destination = `${output}/${app}`;
  await mkdir(destination, { recursive: true });
  if (app === "web") {
    await mkdir(imageDirectory, { recursive: true });
    await sharp(`${root}/apps/web/public/opengraph.png`)
      .resize({ width: 640 })
      .webp({ quality: 75 })
      .toFile(`${imageDirectory}/cover.webp`);
  }
  const origin = await freePort();
  const fixture = await loadFixture(
    app === "web"
      ? `${root}/apps/web/tests/fixtures.ts`
      : `${root}/apps/admin/tests/fixtures/overview.ts`,
  );
  const { server, unknown } = fixtureServer(app, fixture, origin);
  const upstream = await listen(server);
  const urls =
    app === "web"
      ? [`${origin}/latest`, `${origin}/articles/${fixture.article.slug}`]
      : [`${origin}/`];
  const appEnv =
    app === "web"
      ? {
          DEVFEED_PUBLIC_API_URL: upstream,
          DEVFEED_USER_API_URL: upstream,
          DEVFEED_USER_BASE_URL: origin,
        }
      : { DEVFEED_ADMIN_API_URL: upstream, DEVFEED_ADMIN_BASE_URL: origin };
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
    { cwd: `${root}/apps/${app}`, env: { ...env, ...appEnv }, stdio: ["ignore", "pipe", "pipe"] },
  );
  let logs = "";
  next.stdout.on("data", (chunk) => (logs += chunk));
  next.stderr.on("data", (chunk) => (logs += chunk));
  try {
    await waitForReady(next, origin);
    await checkPageContent(app, urls);
    assert.deepEqual([...unknown], [], "Some API fixtures are missing");
    const config = lighthouseConfig(app, urls, chromium.executablePath(), `${destination}/html`);
    const configPath = `${destination}/config.json`;
    await writeFile(configPath, JSON.stringify(config, null, 2));
    await run(process.execPath, [lhci, "collect", `--config=${configPath}`], destination);
    const raw = `${destination}/.lighthouseci`;
    const reports = await Promise.all(
      (await readdir(raw))
        .filter((name) => /^lhr-.*\.json$/.test(name))
        .map(async (name) => JSON.parse(await readFile(`${raw}/${name}`, "utf8"))),
    );
    checkMeasurements(reports, urls, runs);
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
    const summary = measurementSummary(app, reports, urls, assertions);
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
for (const app of ["web", "admin"]) {
  try {
    await run("npm", ["run", `${app}:build`]);
    await audit(app);
  } catch (error) {
    failed = true;
    console.error(error);
    const message = `\n${app} browser budgets failed: ${error.message}\n`;
    await appendFile(`${output}/summary.md`, message);
    if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, message);
  } finally {
    await rm(imageDirectory, { recursive: true, force: true });
  }
}
process.exitCode = failed ? 1 : 0;

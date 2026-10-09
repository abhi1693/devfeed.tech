import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";
import lighthouse from "lighthouse";
import { launch } from "chrome-launcher";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const bundle = await build({
  entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const requests = [];
const fixture = createServer((req, res) => {
  const url = new URL(req.url, "http://fixture");
  requests.push(url);
  const signedIn = req.headers.cookie?.includes("devfeed_user_session=valid");
  let body = {};
  if (url.pathname === "/v1/user/auth/me")
    body = signedIn
      ? { user_id: "user", name: "Reader", csrf_token: "test", expires_at: 4102444800 }
      : null;
  else if (url.pathname === "/v1/user/auth/config") body = { enabled: true, providers: [] };
  else if (url.pathname.includes("/feed/options"))
    body = { content_types: ["article", "news"], sources: [source], topics: [topic] };
  else if (url.pathname.endsWith("/settings/feed"))
    body = { view: "cards", content_types: ["article", "news"], languages: ["en"] };
  else if (url.pathname.endsWith("/feed"))
    body = {
      items: [article],
      next_cursor: null,
      status: "ready",
      has_interests: true,
      reasons: {},
    };
  else if (url.pathname === "/v1/topics") body = [topic];
  else if (url.pathname === "/v1/sources") body = [source];
  else if (url.pathname.endsWith("/settings/appearance")) body = { theme: "light" };
  else if (url.pathname.endsWith("/engagement")) body = [];
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
    cwd: `${root}/apps/web`,
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: `http://127.0.0.1:${fixture.address().port}`,
      DEVFEED_USER_API_URL: `http://127.0.0.1:${fixture.address().port}`,
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
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      await fetch(`${origin}/login`);
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  const phase = process.env.DEVFEED_ENTRY_BENCH;
  if (phase) {
    const destination = `${root}/reports/home-entry/${phase}`;
    await mkdir(destination, { recursive: true });
    const results = [];
    for (let run = 1; run <= 3; run++) {
      for (const path of ["/", "/latest"]) {
        const chrome = await launch({
          chromePath: chromium.executablePath(),
          chromeFlags: ["--headless", "--no-sandbox"],
        });
        try {
          const result = await lighthouse(`${origin}${path}`, {
            port: chrome.port,
            output: ["json", "html"],
            onlyCategories: ["performance"],
            logLevel: "error",
          });
          const label = path === "/" ? "root" : "latest";
          await writeFile(`${destination}/${label}-${run}.json`, result.report[0]);
          await writeFile(`${destination}/${label}-${run}.html`, result.report[1]);
          const response = await fetch(`${origin}${path}`, { redirect: "manual" });
          results.push({
            path,
            run,
            status: response.status,
            finalUrl: result.lhr.finalDisplayedUrl,
            lcp: result.lhr.audits["largest-contentful-paint"].numericValue,
            redirectSavings: result.lhr.audits.redirects.details.overallSavingsMs,
            lighthouse: result.lhr.lighthouseVersion,
            userAgent: result.lhr.environment.hostUserAgent,
          });
        } finally {
          await chrome.kill();
        }
      }
    }
    await writeFile(`${destination}/summary.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 2));
  } else {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ reducedMotion: "reduce" });
    const page = await context.newPage();
    const documents = [];
    page.on("request", (request) => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame())
        documents.push(request.url());
    });
    const campaign = "utm_source=linkedin&utm_medium=organic&utm_campaign=reader_updates";
    const response = await page.goto(
      `${origin}/?${campaign}&q=engineering&sort=oldest&cursor=next`,
    );
    assert.equal(response.status(), 200);
    await page.locator(".article-card").first().waitFor();
    assert.equal(documents.length, 1);
    assert.equal(new URL(page.url()).pathname, "/");
    assert.equal(new URL(page.url()).searchParams.get("utm_campaign"), "reader_updates");
    assert.ok(
      requests.some(
        (url) =>
          url.pathname === "/v1/feed" &&
          url.searchParams.get("q") === "engineering" &&
          url.searchParams.get("cursor") === "next" &&
          url.searchParams.get("sort") === "oldest" &&
          url.searchParams.get("content_type") === "article",
      ),
    );
    assert.match(
      await page.locator('link[rel="canonical"]').getAttribute("href"),
      /\/latest\?q=engineering/,
    );
    const scoped = await page.goto(`${origin}/?topic=python&content_type=news`);
    assert.equal(scoped.status(), 200);
    await page.locator(".article-card").first().waitFor();
    assert.equal(new URL(page.url()).pathname, "/");
    assert.match(
      await page.locator('link[rel="canonical"]').getAttribute("href"),
      /\/topics\/python\/news$/,
    );
    await mkdir(`${root}/reports/home-entry`, { recursive: true });
    await page.screenshot({ path: `${root}/reports/home-entry/guest.png`, fullPage: true });
    await context.addCookies([{ name: "devfeed_user_session", value: "valid", url: origin }]);
    await page.goto(`${origin}/?q=personal&cursor=mine`);
    await page.getByRole("link", { name: "Read later", exact: true }).waitFor();
    await page.locator(".article-card").first().waitFor();
    assert.ok(
      requests.some(
        (url) =>
          url.pathname === "/v1/user/feed" &&
          url.searchParams.get("q") === "personal" &&
          url.searchParams.get("cursor") === "mine",
      ),
    );
    assert.equal(
      await page.locator('meta[name="robots"]').getAttribute("content"),
      "noindex, nofollow",
    );
    await context.clearCookies();
    const guestAgain = await page.goto(`${origin}/`);
    assert.equal(guestAgain.status(), 200);
    await page.locator(".article-card").first().waitFor();
    assert.doesNotMatch(
      (await page.locator('meta[name="robots"]').getAttribute("content")) ?? "",
      /noindex/,
    );
    console.log(
      "Homepage browser checks passed: one guest document, query/attribution preservation, canonical metadata, signed-in personal feed.",
    );
  }
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

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
let held;
let visible = true;
const fixture = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture");
  requests.push(url);
  if (held && !url.pathname.startsWith("/v1/user/")) await held;
  const signedIn = /devfeed_user_session=reader_[ab]/.test(req.headers.cookie ?? "");
  let body = {};
  if (url.pathname === "/v1/user/auth/me")
    body = signedIn
      ? { user_id: "user", name: "Reader", csrf_token: "test", expires_at: 4102444800 }
      : null;
  else if (url.pathname === "/v1/user/auth/config") body = { enabled: true, providers: [] };
  else if (url.pathname.includes("/feed/options"))
    body = { content_types: ["article", "news"], sources: [source], topics: [topic] };
  else if (url.pathname.endsWith("/settings/feed"))
    body = {
      view: "cards",
      content_types: ["article", "news"],
      languages: [req.headers.cookie?.includes("reader_b") ? "fr" : "en"],
    };
  else if (url.pathname.endsWith("/feed"))
    body = {
      items: visible ? [article] : [],
      next_cursor: null,
      status: "ready",
      has_interests: true,
      reasons: {},
    };
  else if (url.pathname === "/v1/topics") body = visible ? [topic] : [];
  else if (url.pathname.startsWith("/v1/topics/") || url.pathname.startsWith("/v1/sources/")) {
    res.statusCode = 404;
    body = { detail: "Not found" };
  } else if (url.pathname === "/v1/sources") body = visible ? [source] : [];
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
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  const nonces = new Set();
  for (const [path, label, ready] of [
    ["/latest", "Loading articles…", ".article-card"],
    ["/sources", "Loading sources…", ".topic-grid"],
    ["/topics", "Loading topics…", ".topic-grid"],
  ]) {
    let release;
    held = new Promise((resolve) => {
      release = resolve;
    });
    try {
      const response = await page.goto(`${origin}${path}`, { waitUntil: "commit" });
      assert.equal(response.status(), 200);
      assert.match(response.headers()["cache-control"], /no-store/);
      const nonce = response.headers()["content-security-policy"].match(/'nonce-([^']+)'/)[1];
      assert.ok(!nonces.has(nonce));
      nonces.add(nonce);
      await page.getByRole("status", { name: label, exact: true }).waitFor();
      assert.equal(
        await page.locator(ready).count(),
        0,
        "data remains blocked while the shell renders",
      );
      assert.ok(await page.locator('link[rel="canonical"]').getAttribute("href"));
      await mkdir(`${root}/reports/document-latency`, { recursive: true });
      await page.screenshot({
        path: `${root}/reports/document-latency/${path.slice(1)}-loading.png`,
      });
    } finally {
      held = undefined;
      release();
    }
    await page.locator(ready).first().waitFor();
    await page.getByRole("status", { name: label, exact: true }).waitFor({ state: "hidden" });
  }
  for (const [value, language] of [
    ["reader_a", "en"],
    ["reader_b", "fr"],
  ]) {
    await context.addCookies([{ name: "devfeed_user_session", value, url: origin }]);
    requests.length = 0;
    await page.goto(`${origin}/latest`);
    await page.locator(".article-card").first().waitFor();
    assert.ok(
      requests.some(
        (url) => url.pathname === "/v1/feed" && url.searchParams.get("languages") === language,
      ),
    );
    assert.ok(
      requests.some(
        (url) =>
          url.pathname === "/v1/feed/options" && url.searchParams.get("languages") === language,
      ),
    );
  }
  await context.clearCookies();
  visible = false;
  await page.goto(`${origin}/latest`);
  await page.getByRole("heading", { name: "No articles match these filters" }).waitFor();
  assert.equal(
    await page.locator(".article-card").count(),
    0,
    "a new document must not reuse withdrawn content",
  );
  for (const kind of ["sources", "topics"]) {
    await page.goto(`${origin}/${kind}`);
    await page.getByRole("heading", { name: `No ${kind} with published articles yet` }).waitFor();
  }
  const redirect = await page.request.get(`${origin}/latest?content_type=news`, {
    maxRedirects: 0,
  });
  assert.equal(redirect.status(), 308);
  assert.equal(redirect.headers().location, "/news");
  for (const path of ["/topics/missing", "/sources/missing"]) {
    const response = await page.goto(`${origin}${path}`);
    assert.equal(
      response.status(),
      404,
      "detail visibility checks retain HTTP 404 before streaming",
    );
  }
  console.log(
    "Public streaming passed: blocked reads, initial metadata, fresh nonces, two users, withdrawn content, detail 404s.",
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  app.kill("SIGTERM");
  await new Promise((resolve) => fixture.close(resolve));
}

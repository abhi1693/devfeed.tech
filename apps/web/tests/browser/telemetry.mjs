import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const appName = process.argv[2] || "web";
assert.ok(["web", "admin"].includes(appName));
const { version } = JSON.parse(await readFile(`${root}/apps/${appName}/package.json`, "utf8"));
const serverKey = "synthetic-server-only-collector-key";
const forwarded = [];
const fixture = createServer(async (request, response) => {
  if (request.url === "/collect") {
    let body = "";
    for await (const chunk of request) body += chunk;
    forwarded.push({ headers: request.headers, body: JSON.parse(body) });
    response.writeHead(202);
    response.end();
    return;
  }
  response.writeHead(401, { "Content-Type": "application/json" });
  response.end(JSON.stringify({ detail: "Not authenticated" }));
});
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
async function availablePort() {
  const reservation = createServer();
  await new Promise((resolve) => reservation.listen(0, "127.0.0.1", resolve));
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  return port;
}
const port = await availablePort();
let metricsPort = await availablePort();
while (metricsPort === port) metricsPort = await availablePort();
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
    cwd: `${root}/apps/${appName}`,
    env: {
      ...env,
      NODE_ENV: "production",
      DEVFEED_VERSION: "stale-runtime-version",
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_ADMIN_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_ADMIN_BASE_URL: origin,
      DEVFEED_FARO_ENABLED: "true",
      DEVFEED_FARO_COLLECTOR_URL: `${upstream}/collect`,
      DEVFEED_FARO_API_KEY: serverKey,
      DEVFEED_TELEMETRY_ENVIRONMENT: "production",
      DEVFEED_METRICS_ENABLED: "true",
      DEVFEED_METRICS_HOST: "127.0.0.1",
      DEVFEED_METRICS_PORT: String(metricsPort),
      DEVFEED_OTLP_ENDPOINT: "",
      DEVFEED_PYROSCOPE_SERVER: "",
      DEVFEED_ANALYTICS_ENABLED: "false",
      DEVFEED_X_PIXEL_ENABLED: "false",
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
let browser;
try {
  const path = appName === "web" ? "/legal/privacy" : "/login";
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (app.exitCode !== null) throw new Error(logs);
    try {
      const response = await fetch(`${origin}${path}`, { signal: AbortSignal.timeout(1000) });
      await response.body?.cancel();
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  assert.ok(ready, logs);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.route(/^https?:\/\//, (route) =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort(),
  );
  page.on("pageerror", (error) => {
    logs += `\nbrowser: ${error.message}`;
  });
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warn")
      logs += `\nbrowser: ${message.text()}`;
  });
  // Select a sampled session deterministically in this test only. Production stays at 10%.
  await page.addInitScript(() => {
    Math.random = () => 0.01;
    const randomValues = crypto.getRandomValues.bind(crypto);
    crypto.getRandomValues = (array) => {
      if (array instanceof Uint32Array && array.length === 1) return array.fill(0);
      return randomValues(array);
    };
  });
  const delivered = page.waitForResponse(
    (response) =>
      response.url() === `${origin}/telemetry/collect` &&
      response.request().method() === "POST" &&
      response
        .request()
        .postDataJSON()
        .events?.some((event) => event.name === "telemetry_ready"),
    { timeout: 20_000 },
  );
  await page.goto(`${origin}${path}`);
  const response = await delivered;
  assert.equal(response.status(), 202);
  const request = response.request();
  assert.equal(await request.headerValue("origin"), origin);
  assert.equal(await request.headerValue("content-encoding"), null);
  assert.equal(await request.headerValue("x-api-key"), null);
  const sent = request.postDataJSON();
  assert.deepEqual(sent.meta.app, {
    name: `devfeed-${appName}`,
    version,
    environment: "production",
  });
  assert.ok(sent.events?.some((event) => event.name === "telemetry_ready"));
  assert.ok(!JSON.stringify(sent).includes(serverKey));
  assert.ok(forwarded.length > 0);
  for (const batch of forwarded) {
    assert.equal(batch.headers["x-api-key"], serverKey);
    assert.equal(batch.headers.origin, origin);
    assert.deepEqual(batch.body.meta.app, sent.meta.app);
  }
  const before = forwarded.length;
  const spoofed = await page.evaluate(
    async () =>
      (
        await fetch("/telemetry/collect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            meta: { app: { name: "spoofed", version: "development", environment: "development" } },
          }),
        })
      ).status,
  );
  assert.equal(spoofed, 202);
  assert.ok(forwarded.length > before);
  assert.deepEqual(forwarded.at(-1).body.meta.app, sent.meta.app);
  const rejected = await fetch(`${origin}/telemetry/collect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  assert.equal(rejected.status, 403);
  const metrics = await (await fetch(`http://127.0.0.1:${metricsPort}/metrics`)).text();
  assert.match(
    metrics,
    /devfeed_faro_deliveries_total\{status="202",outcome="accepted",service="(?:web|admin)"\} [1-9]/,
  );
  assert.match(
    metrics,
    /devfeed_faro_deliveries_total\{status="403",outcome="origin_missing",service="(?:web|admin)"\} 1/,
  );
  assert.ok(metrics.includes('devfeed_request_kind="request"'));
  assert.ok(metrics.includes('http_response_status_code="200"'));
  console.log(
    `${appName}: sampled real Faro browser batch accepted, build metadata authoritative, bounded rejection metrics recorded`,
  );
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  await browser?.close();
  if (app.exitCode === null) {
    const exited = once(app, "exit");
    app.kill("SIGTERM");
    const timer = setTimeout(() => app.kill("SIGKILL"), 5000);
    await exited;
    clearTimeout(timer);
  }
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
}

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const appName = process.argv[2] || "web";
assert.ok(["web", "admin", "partner"].includes(appName));
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
      DEVFEED_PARTNER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: origin,
      DEVFEED_ADMIN_BASE_URL: origin,
      DEVFEED_PARTNER_BASE_URL: origin,
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
const browserErrors = [];
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
    browserErrors.push(error.message);
  });
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warn")
      logs += `\nbrowser: ${message.text()}`;
    if (message.type() === "error" && /Content Security Policy|Refused to/.test(message.text()))
      browserErrors.push(message.text());
  });
  if (appName === "partner") {
    const account = "11111111-1111-4111-8111-111111111111";
    const product = "22222222-2222-4222-8222-222222222222";
    const connection = "33333333-3333-4333-8333-333333333333";
    const query = "private-route-query";
    const requests = [
      ["/products", "/products"],
      [`/products/${product}`, "/products/:id"],
      [`/products/${product}/edit`, "/products/:id/edit"],
      [`/products/${product}/placements`, "/products/:id/placements"],
      [`/products/${product}/archive`, "/products/:id/archive"],
      [`/settings/${account}`, "/settings/:id"],
      [`/settings/${account}/destination`, "/settings/:id/destination"],
      [`/settings/${account}/formats`, "/settings/:id/formats"],
      [`/settings/${account}/audience`, "/settings/:id/audience"],
      [`/connections/${connection}/edit`, "/connections/:id/edit"],
      [`/connections/${connection}/disconnect`, "/connections/:id/disconnect"],
      [`/settings/${account}/unknown-section`, "unmatched"],
      [`/products/${product}/unknown-action`, "unmatched"],
      [`/api/v1/partner/accounts/${account}/products`, "/v1/partner/accounts/:id/products"],
      [
        `/api/v1/partner/accounts/${account}/products/${product}`,
        "/v1/partner/accounts/:id/products/:id",
      ],
    ];
    for (const [requestPath] of requests) {
      await page.goto(`${origin}${requestPath}?account_id=${account}&q=${query}`);
    }
    const metrics = await (await fetch(`http://127.0.0.1:${metricsPort}/metrics`)).text();
    const recorded = metrics
      .split("\n")
      .filter((line) => line.startsWith("devfeed_http_requests_total{"));
    for (const [, label] of requests)
      assert.ok(
        recorded.some((line) => line.includes(`route=${JSON.stringify(label)},`)),
        `${label}: production partner request metrics use the approved route template`,
      );
    for (const privateValue of [account, product, connection, query])
      assert.equal(
        metrics.includes(privateValue),
        false,
        "private route values never label metrics",
      );
    console.log("partner: production requests use bounded labels, private IDs and queries omitted");
  } else {
    const telemetryRequests = [];
    page.on("request", (request) => {
      if (request.url().includes("/telemetry/collect")) telemetryRequests.push(request.url());
    });
    await page.goto(`${origin}${path}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(5500);
    assert.deepEqual(telemetryRequests, [], "retired browser telemetry sends no batches");
    assert.equal(forwarded.length, 0, "collector receives no browser telemetry");
    const metrics = await (await fetch(`http://127.0.0.1:${metricsPort}/metrics`)).text();
    assert.match(metrics, /devfeed_http_requests_total/);
    assert.equal(metrics.includes("devfeed_faro_deliveries_total"), false);
    console.log(`${appName}: browser telemetry retired; server metrics remain active`);
  }
  assert.deepEqual(browserErrors, [], "actual browser has no runtime or CSP errors");
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

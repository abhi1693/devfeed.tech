import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createServer } from "node:http";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { build } from "esbuild";
import { chromium } from "playwright";
import { launch } from "chrome-launcher";
import lighthouse from "lighthouse";
import desktopConfig from "lighthouse/core/config/desktop-config.js";

const phase = process.argv[2];
assert.ok(["before", "after"].includes(phase), "Pass before or after");
const output = new URL(`../../reports/document-latency/${phase}/`, import.meta.url);
await mkdir(output, { recursive: true });
const bundle = await build({
  entryPoints: ["apps/web/tests/fixtures.ts"],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const listen = async (server) => {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return `http://127.0.0.1:${server.address().port}`;
};
const close = async (server) => {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const traces = [];
const collector = createServer(async (req, res) => {
  const parts = [];
  for await (const part of req) parts.push(part);
  const body = JSON.parse(Buffer.concat(parts).toString());
  for (const resource of body.resourceSpans ?? [])
    for (const scope of resource.scopeSpans ?? []) traces.push(...scope.spans);
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end("{}");
});
const collectorOrigin = await listen(collector);
const requests = [];
const items = Array.from({ length: 24 }, (_, i) => ({
  ...article,
  id: `22222222-2222-4222-8222-${String(i + 1).padStart(12, "0")}`,
  slug: `${article.slug}-${i}`,
  title: `${article.title} ${i}`,
}));
const catalog = (item) =>
  Array.from({ length: 60 }, (_, i) => ({
    ...item,
    id: `11111111-1111-4111-8111-${String(i + 1).padStart(12, "0")}`,
    slug: `${item.slug}-${i}`,
    name: `${item.name} ${i}`,
  }));
let delayMs = 80;
const bodies = new Map([
  ["/v1/feed", { items, next_cursor: null }],
  ["/v1/feed/options", { sources: [source], content_types: ["tutorial"], languages: ["en"] }],
  ["/v1/topics", catalog(topic)],
  ["/v1/sources", catalog(source)],
  [`/v1/topics/${topic.slug}`, topic],
  [`/v1/articles/${article.slug}`, article],
]);
const upstream = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture");
  const start = performance.now();
  const row = {
    path: url.pathname,
    query: url.search,
    trace: req.headers.traceparent,
    cookie: req.headers.cookie,
    start,
  };
  requests.push(row);
  await sleep(delayMs);
  const signedIn = /devfeed_user_session=(alpha|beta)/.test(req.headers.cookie ?? "");
  const identity = url.pathname === "/v1/user/auth/me";
  const preferences = url.pathname === "/v1/user/settings/feed";
  const body = identity
    ? signedIn
      ? { user_id: "fixture-reader", name: "Fixture reader" }
      : null
    : preferences
      ? signedIn
        ? { content_types: ["tutorial"], languages: ["en"] }
        : {}
      : bodies.get(url.pathname);
  res.writeHead(preferences && !signedIn ? 401 : body === undefined ? 404 : 200, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body ?? {}));
  row.durationMs = performance.now() - start;
});
const upstreamOrigin = await listen(upstream);
const rows = [];
const audits = [];
const lighthouseOnly = process.argv.includes("--lighthouse-only");
const env = Object.fromEntries(
  Object.entries(process.env).filter(
    ([key]) => !/^(DEVFEED_|GOOGLE_ANALYTICS_|MICROSOFT_CLARITY_|FARO_|OTEL_)/.test(key),
  ),
);
try {
  for (const { route, cookie = "" } of [
    { route: "/" },
    { route: "/latest" },
    { route: "/sources" },
    { route: "/topics" },
    { route: `/topics/${topic.slug}` },
    { route: "/", cookie: "devfeed_user_session=alpha" },
    { route: "/", cookie: "devfeed_user_session=expired" },
  ]) {
    if (lighthouseOnly && (cookie || !["/", "/latest", "/sources"].includes(route))) continue;
    const probe = createServer();
    const origin = await listen(probe);
    await close(probe);
    const metricProbe = createServer();
    const metricOrigin = await listen(metricProbe);
    await close(metricProbe);
    const app = spawn(
      process.execPath,
      [
        "../../node_modules/next/dist/bin/next",
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        new URL(origin).port,
      ],
      {
        cwd: "apps/web",
        env: {
          ...env,
          NEXT_TELEMETRY_DISABLED: "1",
          NEXT_OTEL_VERBOSE: "1",
          DEVFEED_PUBLIC_API_URL: upstreamOrigin,
          DEVFEED_USER_API_URL: upstreamOrigin,
          DEVFEED_USER_BASE_URL: origin,
          DEVFEED_METRICS_ENABLED: "true",
          DEVFEED_METRICS_PORT: new URL(metricOrigin).port,
          DEVFEED_OTLP_ENDPOINT: collectorOrigin,
          DEVFEED_ANALYTICS_ENABLED: "false",
          DEVFEED_FARO_ENABLED: "false",
          DEVFEED_X_PIXEL_ENABLED: "false",
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let logs = "";
    app.stdout.on("data", (chunk) => (logs += chunk));
    app.stderr.on("data", (chunk) => (logs += chunk));
    try {
      let ready = false;
      for (let i = 0; i < 100; i++) {
        try {
          const r = await fetch(origin + "/health");
          await r.text();
          ready = true;
          break;
        } catch {
          await sleep(100);
        }
      }
      assert.ok(ready, logs);
      for (let run = 0; run < (lighthouseOnly ? 0 : 8); run++) {
        delayMs = [240, 80, 140, 60, 120, 90, 180, 100][run];
        const trace = randomBytes(16).toString("hex");
        const span = randomBytes(8).toString("hex");
        const from = requests.length;
        const start = performance.now();
        const response = await fetch(origin + route, {
          headers: {
            traceparent: `00-${trace}-${span}-01`,
            Accept: "text/html",
            ...(cookie ? { Cookie: cookie } : {}),
          },
        });
        const ttfbMs = performance.now() - start;
        const body = await response.text();
        assert.equal(response.status, 200);
        rows.push({
          route,
          run,
          coldRoute: run === 0,
          identity: cookie.includes("alpha") ? "member" : cookie ? "expired" : "guest",
          upstreamDelayMs: delayMs,
          trace,
          ttfbMs,
          completeMs: performance.now() - start,
          bytes: body.length,
          requests: requests.slice(from),
        });
        console.log(JSON.stringify(rows.at(-1)));
      }
      if (lighthouseOnly) {
        delayMs = 80;
        for (const profile of ["mobile", "desktop"]) {
          for (let run = 1; run <= 2; run++) {
            const chrome = await launch({
              chromePath: chromium.executablePath(),
              chromeFlags: ["--headless", "--no-sandbox"],
            });
            try {
              const result = await lighthouse(
                origin + route,
                {
                  port: chrome.port,
                  output: ["json", "html"],
                  onlyAudits: ["server-response-time"],
                  logLevel: "error",
                },
                profile === "desktop" ? desktopConfig : undefined,
              );
              const audit = result.lhr.audits["server-response-time"];
              assert.ok(
                Number.isFinite(audit.numericValue),
                "Lighthouse measured document latency",
              );
              const name = `${profile}-${route === "/" ? "home" : route.slice(1)}-${run}`;
              for (const [i, extension] of ["json", "html"].entries())
                await writeFile(new URL(`${name}.${extension}`, output), result.report[i]);
              const row = {
                route,
                profile,
                run,
                responseMs: audit.numericValue,
                score: audit.score,
                lighthouse: result.lhr.lighthouseVersion,
                browser: result.lhr.environment.hostUserAgent,
              };
              audits.push(row);
              console.log(JSON.stringify(row));
            } finally {
              await chrome.kill();
            }
          }
        }
      }
      await sleep(1200);
    } finally {
      if (app.exitCode === null && app.signalCode === null) {
        const stopped = once(app, "exit");
        const deadline = setTimeout(() => app.kill("SIGKILL"), 5000);
        app.kill("SIGTERM");
        await stopped;
        clearTimeout(deadline);
      }
      await writeFile(
        new URL(
          `${route.replaceAll("/", "_") || "home"}-${cookie.split("=").at(-1) || "guest"}.log`,
          output,
        ),
        logs,
      );
    }
  }
  const duration = (span) => (Number(span.endTimeUnixNano) - Number(span.startTimeUnixNano)) / 1e6;
  for (const row of rows) {
    const spans = traces.filter((span) => span.traceId === row.trace);
    row.serverTimings = {
      metadataMs: spans
        .filter((span) => span.name.startsWith("generateMetadata"))
        .map((span) => ({ name: span.name, ms: duration(span) })),
      renderMs: spans.filter((span) => span.name.startsWith("render route (app)")).map(duration),
      upstreamMs: row.requests.map(({ path, durationMs }) => ({ path, ms: durationMs })),
    };
    assert.ok(row.serverTimings.metadataMs.length, "Capture actual metadata spans");
    for (const request of row.requests.filter(({ path }) => !path.startsWith("/v1/user/")))
      assert.equal(request.cookie, undefined, "Public reads omit reader credentials");
  }
  if (rows.length)
    await writeFile(
      new URL("measurements.json", output),
      JSON.stringify({ rows, traces }, null, 2),
    );
  if (audits.length)
    await writeFile(new URL("lighthouse.json", output), JSON.stringify(audits, null, 2));
} finally {
  await close(upstream);
  await close(collector);
}

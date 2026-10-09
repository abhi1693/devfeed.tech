import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

// Read-only public-page observation. Never save HTML, cookies, request bodies,
// query strings, nonce values, beacon configuration, or visitor identifiers.
const target = new URL(process.env.DEVFEED_CF_TARGET ?? "https://devfeed.tech");
assert.ok(["http:", "https:"].includes(target.protocol));
const runs = Number(process.env.DEVFEED_CF_RUNS ?? 3);
assert.ok(Number.isInteger(runs) && runs > 0 && runs <= 10);
const beacon = (url) => new URL(url).hostname === "static.cloudflareinsights.com";
const rum = (url) => new URL(url).pathname === "/cdn-cgi/rum";
const round = (value) => Math.round(value * 10) / 10;
const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const report = { target: target.origin, date: new Date().toISOString(), origin: null, samples: [] };
if (process.env.DEVFEED_CF_ORIGIN) {
  report.origin = [];
  for (const route of ["/latest", "/topics"]) {
    const response = await fetch(new URL(route, process.env.DEVFEED_CF_ORIGIN), {
      headers: { Host: target.host, Accept: "text/html" },
    });
    assert.equal(response.status, 200);
    const html = await response.text();
    const count = (html.match(/<script\b[^>]*static\.cloudflareinsights\.com/gi) ?? []).length;
    assert.equal(count, 0, "The origin must not duplicate the edge beacon");
    report.origin.push({ route, status: response.status, beaconScripts: count });
  }
}
const browser = await chromium.launch({ headless: true });
try {
  report.browser = browser.version();
  for (const formFactor of ["desktop", "mobile"]) {
    for (const route of ["/latest", "/topics"]) {
      for (let run = 1; run <= runs; run++) {
        // Alternate pair order to avoid attributing a warm edge/cache to blocking.
        for (const blocked of run % 2 ? [false, true] : [true, false]) {
          const mobile = formFactor === "mobile";
          const context = await browser.newContext({
            viewport: mobile ? { width: 412, height: 823 } : { width: 1350, height: 940 },
            deviceScaleFactor: mobile ? 1.75 : 1,
            isMobile: mobile,
            hasTouch: mobile,
          });
          try {
            const page = await context.newPage();
            const cdp = await context.newCDPSession(page);
            await cdp.send("Network.enable");
            await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
            await cdp.send("Emulation.setCPUThrottlingRate", { rate: mobile ? 4 : 1 });
            // Actual CDP throttling, not Lighthouse's simulated performance model.
            await cdp.send("Network.emulateNetworkConditions", {
              offline: false,
              latency: mobile ? 150 : 0,
              downloadThroughput: mobile ? 1_600_000 / 8 : -1,
              uploadThroughput: mobile ? 750_000 / 8 : -1,
            });
            if (blocked)
              await cdp.send("Network.setBlockedURLs", {
                urls: ["*://static.cloudflareinsights.com/*"],
              });
            let start;
            const requests = new Map();
            cdp.on("Network.requestWillBeSent", (event) => {
              if (event.type === "Document" && start === undefined) start = event.timestamp;
              if (!beacon(event.request.url) && !rum(event.request.url)) return;
              requests.set(event.requestId, {
                kind: beacon(event.request.url) ? "beacon" : "rum",
                startMs: round((event.timestamp - start) * 1000),
                initialPriority: event.request.initialPriority,
                initiator: event.initiator.type,
                type: event.type,
              });
            });
            cdp.on("Network.resourceChangedPriority", (event) => {
              const request = requests.get(event.requestId);
              if (request) request.finalPriority = event.newPriority;
            });
            cdp.on("Network.responseReceived", (event) => {
              const request = requests.get(event.requestId);
              if (!request) return;
              request.status = event.response.status;
              request.cacheControl = Object.entries(event.response.headers).find(
                ([name]) => name.toLowerCase() === "cache-control",
              )?.[1];
            });
            cdp.on("Network.loadingFinished", (event) => {
              const request = requests.get(event.requestId);
              if (request)
                Object.assign(request, {
                  endMs: round((event.timestamp - start) * 1000),
                  encodedBytes: event.encodedDataLength,
                });
            });
            cdp.on("Network.loadingFailed", (event) => {
              const request = requests.get(event.requestId);
              if (request) request.blocked = Boolean(event.blockedReason);
            });
            await page.addInitScript(() => {
              window.__cfObservation = { lcp: null, longTasks: 0, cspViolations: 0 };
              new PerformanceObserver((list) => {
                window.__cfObservation.lcp = list.getEntries().at(-1).startTime;
              }).observe({ type: "largest-contentful-paint", buffered: true });
              new PerformanceObserver((list) => {
                window.__cfObservation.longTasks += list
                  .getEntries()
                  .reduce((sum, entry) => sum + entry.duration, 0);
              }).observe({ type: "longtask", buffered: true });
              document.addEventListener("securitypolicyviolation", (event) => {
                if (/cloudflareinsights|\/cdn-cgi\/rum/.test(event.blockedURI))
                  window.__cfObservation.cspViolations++;
              });
            });
            const response = await page.goto(new URL(route, target).href, { waitUntil: "load" });
            assert.equal(response.status(), 200);
            const links =
              route === "/latest" ? 'main a[href^="/articles/"]' : 'main a[href^="/topics/"]';
            await page.locator(links).first().waitFor();
            const readerReadyMs = await page.evaluate(() => performance.now());
            await page.waitForTimeout(4000);
            const observation = await page.evaluate(() => {
              const navigation = performance.getEntriesByType("navigation")[0];
              return {
                ...window.__cfObservation,
                fcp: performance.getEntriesByName("first-contentful-paint")[0]?.startTime,
                domContentLoaded: navigation.domContentLoadedEventEnd,
                load: navigation.loadEventEnd,
                scripts: [
                  ...document.querySelectorAll('script[src*="static.cloudflareinsights.com"]'),
                ].map((script) => ({
                  type: script.type,
                  async: script.async,
                  defer: script.defer,
                  noncePresent: Boolean(script.nonce),
                })),
              };
            });
            assert.equal(observation.scripts.length, 1, "Exactly one edge beacon is injected");
            assert.equal(observation.cspViolations, 0, "Retain the existing nonce/CSP integration");
            const network = [...requests.values()];
            if (blocked)
              assert.equal(network.filter((request) => request.kind === "rum").length, 0);
            else
              assert.ok(
                network.some(
                  (request) =>
                    request.kind === "rum" && request.status >= 200 && request.status < 300,
                ),
                "Retained beacon must deliver RUM",
              );
            report.samples.push({
              formFactor,
              route,
              run,
              blocked,
              readerReadyMs: round(readerReadyMs),
              observation,
              network,
            });
            console.log(
              `${formFactor} ${route} ${run} blocked=${blocked}: reader=${round(readerReadyMs)}ms`,
            );
          } finally {
            await context.close();
          }
        }
      }
    }
  }
} finally {
  await browser.close();
  await mkdir("reports/cloudflare-telemetry", { recursive: true });
  await writeFile("reports/cloudflare-telemetry/trace.json", JSON.stringify(report, null, 2));
}
const summaries = [];
for (const formFactor of ["desktop", "mobile"])
  for (const route of ["/latest", "/topics"]) {
    const samples = report.samples.filter(
      (sample) => sample.formFactor === formFactor && sample.route === route,
    );
    const values = (blocked, pick) =>
      median(samples.filter((sample) => sample.blocked === blocked).map(pick));
    summaries.push({
      formFactor,
      route,
      retainedFcp: round(values(false, (sample) => sample.observation.fcp)),
      blockedFcp: round(values(true, (sample) => sample.observation.fcp)),
      retainedLcp: round(values(false, (sample) => sample.observation.lcp)),
      blockedLcp: round(values(true, (sample) => sample.observation.lcp)),
      retainedReaderReady: values(false, (sample) => sample.readerReadyMs),
      blockedReaderReady: values(true, (sample) => sample.readerReadyMs),
    });
  }
await writeFile("reports/cloudflare-telemetry/summary.json", JSON.stringify(summaries, null, 2));
console.log(JSON.stringify(summaries, null, 2));

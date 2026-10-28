import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { createServer as createSecureServer } from "node:https";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "esbuild";
import sharp from "sharp";
import lighthouse from "lighthouse";
import { launch } from "chrome-launcher";
import { chromium } from "playwright";

const phase = process.argv[2];
assert.ok(["before", "after"].includes(phase), "Pass before or after, then a local source image");
assert.ok(process.argv[3], "A local source image is required for reproducible comparisons");
const root = path.resolve(import.meta.dirname, "../..");
const output = path.join(root, "reports/responsive-thumbnails", phase);
await mkdir(output, { recursive: true });
const original = await readFile(path.resolve(process.argv[3]));
const inputHash = createHash("sha256").update(original).digest("hex");
const widths = phase === "before" ? [320, 640, 960] : [320, 480, 640, 768, 960];
const bodies = new Map(
  await Promise.all(
    widths.map(async (width) => [
      width,
      await sharp(original)
        .resize({ width, withoutEnlargement: true })
        .webp({ quality: 70 })
        .toBuffer(),
    ]),
  ),
);
const bundle = await build({
  entryPoints: [path.join(root, "apps/web/tests/fixtures.ts")],
  bundle: true,
  write: false,
  format: "esm",
});
const { article, topic, source } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
let origin;
const respond = (req, res) => {
  const url = new URL(req.url, "http://fixture");
  const image = url.pathname.match(/^\/thumbnails\/v2\/\d+\/(\d+)\.webp$/);
  if (image) {
    const body = bodies.get(Number(image[1]));
    res.writeHead(body ? 200 : 404, {
      "Content-Type": "image/webp",
      "Cache-Control": "public, max-age=31536000, immutable",
    });
    res.end(body);
    return;
  }
  const items = Array.from({ length: 24 }, (_, index) => ({
    ...article,
    id: `22222222-2222-4222-8222-${String(index).padStart(12, "0")}`,
    slug: `thumbnail-${index}`,
    image_url: `${origin}/thumbnails/v2/${index}/960.webp`,
    image_variants: widths.map((width) => ({
      width,
      url: `${origin}/thumbnails/v2/${index}/${width}.webp`,
    })),
  }));
  let body = {};
  if (url.pathname === "/v1/user/auth/me") body = null;
  else if (url.pathname.endsWith("/auth/config")) body = { enabled: true, providers: [] };
  else if (url.pathname.includes("/feed/options"))
    body = { sources: [source], content_types: ["tutorial"] };
  else if (url.pathname.endsWith("/feed")) body = { items, next_cursor: null };
  else if (url.pathname === "/v1/topics") body = [topic];
  else if (url.pathname === "/v1/sources") body = [source];
  else if (url.pathname.endsWith("/engagement")) body = [];
  else if (url.pathname.includes("placements")) body = { items: [] };
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
};
const fixture = createServer(respond);
const tls = await mkdtemp(path.join(tmpdir(), "devfeed-thumbnail-tls-"));
execFileSync(
  "openssl",
  [
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-keyout",
    `${tls}/key.pem`,
    "-out",
    `${tls}/cert.pem`,
    "-days",
    "1",
    "-subj",
    "/CN=localhost",
  ],
  { stdio: "ignore" },
);
const images = createSecureServer(
  { key: await readFile(`${tls}/key.pem`), cert: await readFile(`${tls}/cert.pem`) },
  respond,
);
await new Promise((resolve) => images.listen(0, "127.0.0.1", resolve));
await new Promise((resolve) => fixture.listen(0, "127.0.0.1", resolve));
const upstream = `http://127.0.0.1:${fixture.address().port}`;
// Serve fixture images on their own HTTP origin; no production proxy or route is added.
origin = `https://127.0.0.1:${images.address().port}`;
const probe = createServer();
await new Promise((resolve) => probe.listen(0, "127.0.0.1", resolve));
const port = probe.address().port;
await new Promise((resolve) => probe.close(resolve));
// Image URLs point directly at the fixture server, preserving independent image requests.
const appOrigin = `http://127.0.0.1:${port}`;
const env = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !/^(DEVFEED_|OTEL_)/.test(key)),
);
const app = spawn(
  process.execPath,
  [
    path.join(root, "node_modules/next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ],
  {
    cwd: path.join(root, "apps/web"),
    env: {
      ...env,
      DEVFEED_PUBLIC_API_URL: upstream,
      DEVFEED_USER_API_URL: upstream,
      DEVFEED_USER_BASE_URL: appOrigin,
    },
    stdio: "pipe",
  },
);
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
try {
  for (let attempt = 0; ; attempt++) {
    try {
      await fetch(`${appOrigin}/login`);
      break;
    } catch (error) {
      if (attempt >= 100) throw new Error(logs, { cause: error });
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  const results = [];
  for (const desktop of [false, true]) {
    for (let run = 1; run <= 3; run++) {
      const chrome = await launch({
        chromePath: chromium.executablePath(),
        chromeFlags: ["--headless", "--no-sandbox", "--ignore-certificate-errors"],
      });
      try {
        const result = await lighthouse(`${appOrigin}/latest`, {
          port: chrome.port,
          output: ["json", "html"],
          onlyCategories: ["performance"],
          logLevel: "error",
          formFactor: desktop ? "desktop" : "mobile",
          screenEmulation: {
            mobile: !desktop,
            width: desktop ? 1350 : 412,
            height: desktop ? 940 : 823,
            deviceScaleFactor: desktop ? 1 : 1.75,
            disabled: false,
          },
          ...(desktop
            ? {
                throttling: {
                  rttMs: 40,
                  throughputKbps: 10240,
                  cpuSlowdownMultiplier: 1,
                  requestLatencyMs: 0,
                  downloadThroughputKbps: 0,
                  uploadThroughputKbps: 0,
                },
              }
            : {}),
        });
        assert.equal(result.lhr.runtimeError, undefined);
        const label = `${desktop ? "desktop" : "mobile"}-${run}`;
        await writeFile(`${output}/${label}.json`, result.report[0]);
        await writeFile(`${output}/${label}.html`, result.report[1]);
        if (run === 1) {
          const screenshot = result.lhr.audits["final-screenshot"]?.details?.data;
          if (screenshot)
            await writeFile(
              `${output}/${label}.jpg`,
              Buffer.from(screenshot.split(",")[1], "base64"),
            );
        }
        const requests = result.lhr.audits["network-requests"].details.items.filter((item) =>
          item.url.includes("/thumbnails/"),
        );
        assert.ok(
          requests.length > 0 && requests.every((item) => item.resourceSize > 0),
          "Fixture images must load successfully",
        );
        const imageAudit = result.lhr.audits["image-delivery-insight"];
        const responsive = result.lhr.audits["uses-responsive-images"];
        results.push({
          label,
          inputHash,
          widths,
          quality: 70,
          lighthouse: result.lhr.lighthouseVersion,
          userAgent: result.lhr.environment.hostUserAgent,
          settings: result.lhr.configSettings,
          requests: requests.map(({ url, transferSize, resourceSize }) => ({
            path: new URL(url).pathname,
            transferSize,
            resourceSize,
          })),
          imageTransferBytes: requests.reduce((sum, item) => sum + item.transferSize, 0),
          responsiveWasteBytes: responsive?.details?.overallSavingsBytes ?? null,
          imageDelivery: imageAudit?.details ?? null,
        });
        console.log(label, results.at(-1).imageTransferBytes);
      } finally {
        await chrome.kill();
      }
    }
  }
  await writeFile(`${output}/summary.json`, JSON.stringify(results, null, 2));
} catch (error) {
  console.error(logs);
  throw error;
} finally {
  const stopped = new Promise((resolve) => app.once("exit", resolve));
  app.kill("SIGTERM");
  await stopped;
  fixture.closeAllConnections();
  await new Promise((resolve) => fixture.close(resolve));
  images.closeAllConnections();
  await new Promise((resolve) => images.close(resolve));
  await rm(tls, { recursive: true, force: true });
}

// Same-avatar comparison against the real production-built WebP route.
// Run after npm run web:build. Saved inputs/reports/screenshots remain ignored.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const root = path.resolve("reports/avatar-delivery");
await mkdir(root, { recursive: true });
const ids = ["5083532", "583231"];
const widths = [32, 64, 128, 256, 512];
const input = [];
for (const id of ids) {
  for (const width of widths) {
    const file = path.join(root, `${id}-${width}.png`);
    let body;
    try {
      body = await readFile(file);
    } catch {
      const response = await fetch(`https://avatars.githubusercontent.com/u/${id}?v=4&s=${width}`);
      assert.ok(response.ok);
      body = Buffer.from(await response.arrayBuffer());
      await writeFile(file, body);
    }
    input.push({
      id,
      width,
      bytes: body.length,
      sha256: createHash("sha256").update(body).digest("hex"),
    });
  }
}
await writeFile(path.join(root, "inputs.json"), JSON.stringify(input, null, 2) + "\n");
const appPort = Number(process.env.DEVFEED_AVATAR_BENCH_PORT || 18727);
const appOrigin = `http://127.0.0.1:${appPort}`;
const app = spawn(
  process.execPath,
  [
    "node_modules/next/dist/bin/next",
    "start",
    "apps/web",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(appPort),
  ],
  { stdio: "pipe" },
);
let logs = "";
app.stdout.on("data", (data) => (logs += data));
app.stderr.on("data", (data) => (logs += data));
const gallery = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/") {
      const after = url.searchParams.get("phase") === "after";
      const candidates = after ? [32, 64, 96, 128, 192, 256, 512] : widths;
      const images = ids
        .map((id) => {
          const resized = (width) =>
            after ? `${appOrigin}/api/avatars/github/${id}/${width}?v=4` : `/${id}-${width}.png`;
          return `<span class="avatar"><img alt="" src="${resized(128)}" srcset="${candidates.map((width) => `${resized(width)} ${width}w`).join(",")}" sizes="(max-width:540px) ${after ? 84 : 96}px, ${after ? 104 : 116}px"></span>`;
        })
        .join("");
      res.writeHead(200, { "Content-Type": "text/html", "Cache-Control": "no-store" });
      res.end(
        `<!doctype html><html lang="en"><title>Profile avatar delivery</title><meta name="viewport" content="width=device-width"><style>*{box-sizing:border-box}.avatar{display:inline-block;width:116px;height:116px;border:6px solid white}.avatar img{width:100%;height:100%;object-fit:cover}@media(max-width:540px){.avatar{width:96px;height:96px}}</style><h1>Profile avatars</h1>${images}</html>`,
      );
    } else {
      const filename = url.pathname.slice(1);
      if (!/^\d+-\d+\.png$/.test(filename)) throw new Error("missing fixture");
      const body = await readFile(path.join(root, filename));
      res.writeHead(200, { "Content-Type": "image/png", "Cache-Control": "public, max-age=300" });
      res.end(body);
    }
  } catch {
    res.writeHead(404);
    res.end();
  }
});
let browser;
const results = [];
try {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const response = await fetch(`${appOrigin}/api/avatars/github/invalid/128`);
      if (response.status === 404) break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (attempt === 99) throw new Error(logs);
  }
  // Exercise the actual route's conditional caching and rejection boundaries.
  const avatar = await fetch(`${appOrigin}/api/avatars/github/5083532/128?v=4`);
  assert.equal(avatar.status, 200, logs);
  assert.equal(avatar.headers.get("content-type"), "image/webp");
  assert.equal(avatar.headers.get("cache-control"), "public, max-age=3600");
  assert.equal(
    (
      await fetch(`${appOrigin}/api/avatars/github/5083532/128?v=4`, {
        headers: { "If-None-Match": avatar.headers.get("etag") },
      })
    ).status,
    304,
  );
  const invalid = await fetch(`${appOrigin}/api/avatars/github/5083532/9999`);
  assert.equal(invalid.status, 404);
  assert.equal(invalid.headers.get("cache-control"), "no-store");
  const privateResponse = await fetch(`${appOrigin}/api/v1/users/invalid%21`);
  assert.match(privateResponse.headers.get("cache-control"), /no-store/);
  await new Promise((resolve) => gallery.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${gallery.address().port}`;
  browser = await chromium.launch({ headless: true });
  for (const [name, width, height, dpr] of [
    ["mobile", 412, 823, 1.75],
    ["desktop", 1350, 940, 1],
    ["retina", 1350, 940, 2],
  ]) {
    for (const phase of ["before", "after"]) {
      const context = await browser.newContext({
        viewport: { width, height },
        deviceScaleFactor: dpr,
      });
      const page = await context.newPage();
      const cdp = await context.newCDPSession(page);
      await cdp.send("Network.enable");
      let images = new Set();
      let bytes = 0;
      let cached = 0;
      cdp.on("Network.responseReceived", ({ requestId, type, response }) => {
        if (type === "Image") {
          images.add(requestId);
          if (response.fromDiskCache) cached++;
        }
      });
      cdp.on("Network.loadingFinished", ({ requestId, encodedDataLength }) => {
        if (images.has(requestId)) bytes += encodedDataLength;
      });
      await page.goto(`${origin}/?phase=${phase}`);
      assert.ok(
        await page
          .locator("img")
          .evaluateAll((images) =>
            images.every((image) => image.complete && image.naturalWidth > 0),
          ),
      );
      const geometry = await page.locator("img").evaluateAll((images) =>
        images.map((image) => ({
          currentSrc: image.currentSrc,
          renderedWidth: image.getBoundingClientRect().width,
          naturalWidth: image.naturalWidth,
        })),
      );
      const cold = bytes;
      await page.screenshot({ path: path.join(root, `${name}-${phase}.png`) });
      images = new Set();
      bytes = 0;
      cached = 0;
      await page.goto(`${origin}/?phase=${phase}&warm=1`);
      const warm = bytes;
      assert.equal(warm, 0, "fresh warm documents reuse image responses");
      results.push({
        name,
        phase,
        dpr,
        coldTransferBytes: cold,
        warmTransferBytes: warm,
        cachedResponses: cached,
        images: geometry,
      });
      await context.close();
    }
  }
  if (process.env.DEVFEED_AVATAR_LIGHTHOUSE === "1") {
    const { default: lighthouse } = await import("lighthouse");
    const { launch } = await import("chrome-launcher");
    const { default: desktop } = await import("lighthouse/core/config/desktop-config.js");
    for (const profile of ["mobile", "desktop"])
      for (const phase of ["before", "after"]) {
        const chrome = await launch({
          chromePath: chromium.executablePath(),
          chromeFlags: ["--headless", "--no-sandbox"],
        });
        try {
          const report = await lighthouse(
            `${origin}/?phase=${phase}`,
            {
              port: chrome.port,
              output: ["json", "html"],
              onlyCategories: ["performance"],
              logLevel: "error",
            },
            profile === "desktop" ? desktop : undefined,
          );
          await writeFile(path.join(root, `${profile}-${phase}-lighthouse.json`), report.report[0]);
          await writeFile(path.join(root, `${profile}-${phase}-lighthouse.html`), report.report[1]);
        } finally {
          await chrome.kill();
        }
      }
  }
  await writeFile(path.join(root, "delivery.json"), JSON.stringify(results, null, 2) + "\n");
  console.log(JSON.stringify(results, null, 2));
} finally {
  await browser?.close();
  gallery.close();
  app.kill("SIGTERM");
}

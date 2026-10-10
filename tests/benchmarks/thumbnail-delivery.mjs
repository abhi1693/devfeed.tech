import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import lighthouse from "lighthouse";
import { launch } from "chrome-launcher";
import { chromium } from "playwright";

const directory = path.resolve("reports/thumbnail-encoding");
const images = ["photo", "screenshot", "text"];
const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture");
  const version = url.pathname.includes("v2") ? "v2" : "v1";
  const width = url.searchParams.get("width") === "960" ? 960 : 640;
  const match = url.pathname.match(
    /^\/thumbnails\/(v1|v2)\/(photo|screenshot|text)\/(640|960)\.webp$/,
  );
  if (match) {
    const body = await readFile(
      path.join(directory, `${match[2]}-${match[3]}-q${match[1] === "v1" ? 78 : 70}.webp`),
    );
    res.writeHead(200, {
      "Content-Type": "image/webp",
      "Cache-Control": "public, max-age=31536000, immutable",
    });
    res.end(body);
  } else {
    res.setHeader("Content-Type", "text/html");
    res.setHeader("Cache-Control", "no-store");
    res.end(
      `<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Thumbnail compression comparison</title></head><body style="margin:16px;font:16px system-ui"><h1>Managed thumbnail compression</h1>${images.map((name) => `<img src="/thumbnails/${version}/${name}/${width}.webp" alt="${name}" style="display:block;width:${width === 960 ? 960 : 360}px;height:auto;margin:16px 0">`).join("")}</body></html>`,
    );
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const summaries = [];
try {
  for (const desktop of [false, true]) {
    for (const version of ["v1", "v2"]) {
      const chrome = await launch({
        chromePath: chromium.executablePath(),
        chromeFlags: ["--headless", "--no-sandbox"],
      });
      try {
        const result = await lighthouse(
          `http://127.0.0.1:${server.address().port}/${version}?width=${desktop ? 960 : 640}`,
          {
            port: chrome.port,
            output: ["json", "html"],
            onlyCategories: ["performance"],
            logLevel: "error",
            ...(desktop
              ? {
                  formFactor: "desktop",
                  screenEmulation: {
                    mobile: false,
                    width: 1350,
                    height: 940,
                    deviceScaleFactor: 1,
                    disabled: false,
                  },
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
          },
        );
        await mkdir(directory, { recursive: true });
        const label = `${desktop ? "desktop" : "mobile"}-${version}`;
        await writeFile(`${directory}/lighthouse-${label}.json`, result.report[0]);
        await writeFile(`${directory}/lighthouse-${label}.html`, result.report[1]);
        const audit = result.lhr.audits["image-delivery-insight"];
        const requests = result.lhr.audits["network-requests"].details.items.filter(
          (item) => item.resourceType === "Image",
        );
        summaries.push({
          label,
          lighthouse: result.lhr.lighthouseVersion,
          userAgent: result.lhr.environment.hostUserAgent,
          imageTransferBytes: requests.reduce((sum, item) => sum + item.transferSize, 0),
          imageResourceBytes: requests.reduce((sum, item) => sum + item.resourceSize, 0),
          audit,
        });
      } finally {
        await chrome.kill();
      }
    }
  }
  await writeFile(`${directory}/delivery.json`, JSON.stringify(summaries, null, 2));
  console.log(
    JSON.stringify(
      summaries.map(({ audit, ...summary }) => ({
        ...summary,
        savings: audit?.metricSavings,
        details: audit?.details,
      })),
      null,
      2,
    ),
  );
} finally {
  await new Promise((resolve) => server.close(resolve));
}

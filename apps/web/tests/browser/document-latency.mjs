import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import {
  documentApiUrl,
  documentReportFilename,
  documentResponseStatus,
  documentCacheStatus,
} from "./document-latency-config.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const upstream = process.env.DEVFEED_DOCUMENT_API_ORIGIN;
assert.ok(upstream, "Set DEVFEED_DOCUMENT_API_ORIGIN to a read-only public API");
const phase = process.env.DEVFEED_DOCUMENT_PHASE ?? "profile";
const reportFilename = documentReportFilename(phase);
const records = [];
const proxy = createServer(async (req, res) => {
  const started = performance.now();
  if (req.method !== "GET") {
    res.writeHead(405);
    res.end("{}");
    return;
  }
  let target;
  try {
    target = documentApiUrl(req.url, upstream);
  } catch {
    res.writeHead(400);
    res.end("{}");
    return;
  }
  const path = target.pathname;
  try {
    const response = await fetch(target, {
      headers: { Accept: "application/json", "Cache-Control": "max-age=600" },
      redirect: "error",
    });
    const body = Buffer.from(await response.arrayBuffer());
    records.push({
      path,
      duration: performance.now() - started,
      cache: documentCacheStatus(response.headers.get("x-cache")),
      status: documentResponseStatus(response.status),
    });
    res.writeHead(response.status, { "Content-Type": "application/json" });
    res.end(body);
  } catch {
    res.writeHead(502);
    res.end("{}");
  }
});
await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
const results = [];
try {
  for (const path of ["/", "/latest", "/sources", "/topics"]) {
    const portProbe = createServer();
    await new Promise((resolve) => portProbe.listen(0, "127.0.0.1", resolve));
    const port = portProbe.address().port;
    await new Promise((resolve) => portProbe.close(resolve));
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
          DEVFEED_PUBLIC_API_URL: `http://127.0.0.1:${proxy.address().port}`,
          DEVFEED_PUBLIC_BASE_URL: origin,
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    try {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("Next startup timed out")), 30000);
        app.stdout.on("data", (data) => {
          if (String(data).includes("Ready")) {
            clearTimeout(timeout);
            resolve();
          }
        });
        app.once("exit", () => {
          clearTimeout(timeout);
          reject(new Error("Next exited"));
        });
      });
      for (let run = 0; run < 6; run++) {
        const offset = records.length;
        const start = performance.now();
        const response = await fetch(`${origin}${path}`, {
          redirect: "manual",
          headers: { "User-Agent": "Mozilla/5.0 Chrome/153.0.0.0 Safari/537.36" },
        });
        const headersMs = performance.now() - start;
        const reader = response.body.getReader();
        const first = await reader.read();
        const firstByteMs = performance.now() - start;
        const chunks = [Buffer.from(first.value ?? [])];
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          chunks.push(Buffer.from(chunk.value));
        }
        const html = Buffer.concat(chunks).toString();
        const csp = response.headers.get("content-security-policy");
        assert.ok(csp?.includes("'nonce-"));
        assert.match(response.headers.get("cache-control") ?? "", /no-store/);
        results.push({
          path,
          state: run === 0 ? "cold-process" : "warm-process",
          run,
          status: documentResponseStatus(response.status),
          headersMs,
          firstByteMs,
          completeMs: performance.now() - start,
          bytes: Buffer.byteLength(html),
          canonicalInFirstChunk: /rel="canonical"/.test(chunks[0].toString()),
          upstream: records.slice(offset),
        });
      }
    } finally {
      app.kill("SIGTERM");
      await new Promise((resolve) => app.once("exit", resolve));
    }
  }
  const directory = `${root}/reports/document-latency`;
  await mkdir(directory, { recursive: true });
  await writeFile(`${directory}/${reportFilename}`, JSON.stringify(results, null, 2));
  for (const path of ["/", "/latest", "/sources", "/topics"]) {
    const rows = results.filter((row) => row.path === path);
    const warm = rows
      .slice(1)
      .map((row) => row.firstByteMs)
      .sort((a, b) => a - b);
    console.log(
      JSON.stringify({
        path,
        cold: rows[0].firstByteMs,
        warmMedian: warm[2],
        warmMin: warm[0],
        warmMax: warm[4],
        upstreamCounts: rows.map((row) => row.upstream.length),
      }),
    );
  }
} finally {
  await new Promise((resolve) => proxy.close(resolve));
}

import assert from "node:assert/strict";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { createServer } from "node:https";
import { chromium } from "playwright";
import { build } from "esbuild";

const browser = process.env.DEVFEED_EXTENSION_BROWSER ?? "chrome";
const extension = path.resolve(import.meta.dirname, `../dist/${browser}`);
const bundle = await build({
  entryPoints: ["apps/web/tests/fixtures.ts"],
  bundle: true,
  write: false,
  format: "esm",
});
const { article } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

test(
  "My Feed scrolls past 24 items while the visible new tab lacks document focus",
  { timeout: 60000 },
  async () => {
    const profile = await mkdtemp(path.join(tmpdir(), "devfeed-pagination-"));
    const cursors = [];
    execFileSync(
      "openssl",
      [
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        path.join(profile, "key.pem"),
        "-out",
        path.join(profile, "cert.pem"),
        "-days",
        "1",
        "-subj",
        "/CN=devfeed.tech",
      ],
      { stdio: "ignore" },
    );
    const server = createServer(
      {
        key: await readFile(path.join(profile, "key.pem")),
        cert: await readFile(path.join(profile, "cert.pem")),
      },
      (req, res) => {
        const url = new URL(req.url, "https://devfeed.tech");
        let value = {};
        if (url.pathname.startsWith("/api/v1/articles/"))
          value = {
            article: { ...article, slug: url.pathname.split("/").at(-1), image_url: null },
            topic: null,
          };
        if (url.pathname === "/api/v1/user/auth/me")
          value = {
            user_id: "11111111-1111-4111-8111-111111111111",
            name: "Pagination Reader",
            email: "reader@example.test",
            expires_at: Math.floor(Date.now() / 1000) + 86400,
            csrf_token: "c".repeat(43),
          };
        else if (url.pathname === "/api/v1/user/must-reads")
          value = {
            date: new Date().toISOString().slice(0, 10),
            timezone: "UTC",
            items: [],
            reasons: {},
            read_ids: [],
            presented: true,
            preparing: false,
          };
        else if (url.pathname === "/api/v1/user/feed") {
          const cursor = url.searchParams.get("cursor");
          cursors.push(cursor);
          const offset = Number(cursor ?? 0);
          value = {
            items: Array.from({ length: 24 }, (_, i) => ({
              ...article,
              id: `11111111-1111-4111-8111-${String(offset + i).padStart(12, "0")}`,
              slug: `pagination-${offset + i}`,
              title: `Pagination article ${offset + i}`,
              image_url: null,
            })),
            next_cursor: offset < 48 ? String(offset + 24) : null,
            generation: "pagination",
            status: "ready",
            has_interests: true,
            reasons: {},
          };
        } else if (url.pathname === "/api/v1/user/settings/feed")
          value = { view: "cards", content_types: ["news"], languages: ["en"] };
        else if (["/api/v1/topics", "/api/v1/sources"].includes(url.pathname))
          value = { items: [], next_cursor: null };
        else if (url.pathname === "/api/v1/feed/options")
          value = { content_types: ["news"], sources: [], languages: ["en"] };
        else if (url.pathname === "/api/v1/user/settings/profile")
          value = { display_name: "Pagination Reader", avatar_url: null };
        else if (url.pathname === "/api/v1/user/engagement") value = [];
        else if (url.pathname === "/api/v1/user/preferences/sources") value = { source_ids: [] };
        else if (url.pathname === "/api/v1/user/settings/appearance") value = { theme: "dark" };
        else if (url.pathname === "/api/v1/user/notifications/config") value = { enabled: false };
        else if (url.pathname === "/api/v1/user/preferences")
          value = { topic_ids: [], source_ids: [] };
        else if (url.pathname === "/api/v1/user/notifications/stream") {
          res.writeHead(200, { "Content-Type": "text/event-stream" });
          res.end();
          return;
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(value));
      },
    );
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    let context;
    try {
      context = await chromium.launchPersistentContext(profile, {
        executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
          ? undefined
          : browser === "edge"
            ? "msedge"
            : "chromium",
        headless: true,
        viewport: { width: 1440, height: 1000 },
        args: [
          `--disable-extensions-except=${extension}`,
          `--load-extension=${extension}`,
          `--host-resolver-rules=MAP devfeed.tech 127.0.0.1:${server.address().port}`,
          "--no-proxy-server",
          "--ignore-certificate-errors",
        ],
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.addInitScript(() =>
        Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false }),
      );
      await page.goto(browser === "edge" ? "edge://newtab" : "chrome://newtab");
      await page.waitForFunction(() => document.querySelectorAll(".article-card").length === 24);
      assert.equal(await page.evaluate(() => document.hasFocus()), false);
      assert.equal(await page.evaluate(() => document.visibilityState), "visible");
      for (const count of [48, 72]) {
        await page.locator(".pagination").last().scrollIntoViewIfNeeded();
        await page.waitForFunction(
          (count) => document.querySelectorAll(".article-card").length === count,
          count,
        );
      }
      assert.deepEqual(cursors, [null, "24", "48"]);
      assert.deepEqual(errors, []);
      await page.screenshot({ path: path.resolve(extension, `../${browser}-pagination.png`) });
      assert.equal(await page.locator(".pagination").last().getAttribute("data-has-more"), "false");
    } finally {
      await context?.close();
      await new Promise((resolve) => server.close(resolve));
      await rm(profile, { recursive: true, force: true });
    }
  },
);

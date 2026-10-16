import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import { chromium } from "playwright";
import { promoPublicProfile } from "../../../../scripts/testing/dev-card-promo.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const publicOrigin = "https://devfeed.tech";

function validateDocument(document, path, article) {
  const graphs = [...document.querySelectorAll('script[type="application/ld+json"]')].map(
    (script) => JSON.parse(script.textContent),
  );
  const profile = path.startsWith("/users/");
  assert.equal(graphs.length, profile ? 1 : 2, `${path} has one site and at most one page graph`);
  const site = graphs.find((graph) => graph["@graph"]);
  assert.deepEqual(
    site["@graph"].map((entity) => entity["@type"]),
    ["Organization", "WebSite"],
  );
  const [organization, website] = site["@graph"];
  assert.equal(website.publisher["@id"], organization["@id"]);
  assert.ok(organization.description);
  assert.ok(organization.logo.width >= 112 && organization.logo.height >= 112);
  assert.ok(organization.logo.url.startsWith(`${publicOrigin}/_next/static/media/`));
  if (profile) {
    assert.match(document.querySelector('meta[name="robots"]').content, /noindex/);
    assert.ok(!JSON.stringify(graphs).includes('"ProfilePage"'));
    return graphs;
  }
  const page = graphs.find(
    (graph) => graph["@type"] === "WebPage" || graph["@type"] === "CollectionPage",
  );
  const canonical = document.querySelector('link[rel="canonical"]').href;
  assert.equal(page.url, canonical);
  assert.equal(page["@id"], `${canonical}#webpage`);
  assert.equal(page.isPartOf["@id"], website["@id"]);
  const crumbs = page.breadcrumb.itemListElement;
  assert.ok(crumbs.length >= 2);
  assert.equal(page.breadcrumb["@id"], `${canonical}#breadcrumb`);
  for (const [index, crumb] of crumbs.entries()) {
    assert.equal(crumb["@type"], "ListItem");
    assert.equal(crumb.position, index + 1);
    assert.ok(crumb.name.trim());
    assert.ok(crumb.item.startsWith(`${publicOrigin}/`));
  }
  assert.equal(crumbs.at(-1).item, canonical);
  const definitions = [];
  function collect(value) {
    if (!value || typeof value !== "object") return;
    if (value["@type"] && value["@id"]) definitions.push(value["@id"]);
    for (const child of Object.values(value)) collect(child);
  }
  collect(graphs);
  assert.equal(
    new Set(definitions).size,
    definitions.length,
    `${path} has no conflicting entity definitions`,
  );
  if (path.startsWith("/articles/")) {
    const entity = page.mainEntity;
    assert.equal(entity["@type"], "Article");
    assert.equal(entity["@id"], article.canonical_url);
    assert.equal(entity.url, article.canonical_url);
    assert.notEqual(entity.url, canonical);
    assert.equal(entity.publisher.name, article.sources[0].name);
    assert.equal(entity.author.name, article.author);
    assert.equal(entity.headline, document.querySelector("#article-preview-title").textContent);
    assert.equal(entity.description, document.querySelector('meta[name="description"]').content);
    assert.equal(page.description, entity.description);
    assert.equal(entity.datePublished, new Date(article.published_at).toISOString());
    assert.ok(!("dateModified" in entity));
    assert.ok(!("image" in entity), "The fallback social card is not an original illustration");
  } else if (page["@type"] === "CollectionPage") {
    assert.equal(page.mainEntity["@type"], "ItemList");
    assert.equal(page.mainEntity["@id"], `${canonical}#items`);
    assert.equal(page.mainEntity.itemListElement[0].position, path.includes("offset=60") ? 61 : 1);
  } else {
    assert.equal(page.description, document.querySelector('meta[name="description"]').content);
    if (path.startsWith("/legal/"))
      assert.equal(page.dateModified, document.querySelector(".legal-updated time").dateTime);
  }
  return graphs;
}

test(
  "public structured data matches crawler HTML, rendered pages and profile privacy",
  { timeout: 180000 },
  async (t) => {
    let app, browser;
    let logs = "";
    const bundle = await build({
      entryPoints: [`${root}/apps/web/tests/fixtures.ts`],
      bundle: true,
      write: false,
      format: "esm",
    });
    const { article, topic, source } = await import(
      `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
    );
    const fixture = createServer((req, res) => {
      const url = new URL(req.url, "http://fixture");
      const path = url.pathname;
      let body = {};
      if (path === "/v1/user/auth/me") body = null;
      else if (path === "/v1/user/auth/config") body = { enabled: true, providers: [] };
      else if (path.includes("/feed/options"))
        body = { content_types: ["article", "news"], sources: [source], topics: [topic] };
      else if (path.endsWith("/settings/feed"))
        body = { view: "cards", content_types: ["article", "news"], languages: ["en"] };
      else if (path.endsWith("/feed")) body = { items: [article], next_cursor: null };
      else if (path === "/v1/topics") body = [topic];
      else if (path === "/v1/sources") body = [source];
      else if (path.startsWith("/v1/topics/") || path.startsWith("/v1/tags/")) body = topic;
      else if (path.startsWith("/v1/sources/")) body = source;
      else if (path.startsWith("/v1/articles/")) body = article;
      else if (path === "/v1/user/profiles/promo-reader") body = promoPublicProfile;
      else if (path.endsWith("/reading-heatmap")) {
        res.statusCode = 404;
        body = { detail: "No activity" };
      } else if (path.endsWith("/settings/appearance")) body = { theme: "light" };
      else if (path.endsWith("/engagement")) body = [];
      else if (path.includes("placements")) body = { items: [] };
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(body));
    });
    t.after(async () => {
      await browser?.close();
      if (app && app.exitCode === null) {
        const stopped = new Promise((resolve) => app.once("exit", resolve));
        app.kill("SIGTERM");
        await stopped;
      }
      fixture.closeAllConnections();
      await new Promise((resolve) => fixture.close(resolve));
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
    app = spawn(
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
          DEVFEED_USER_BASE_URL: publicOrigin,
        },
        stdio: "pipe",
      },
    );
    app.stdout.on("data", (data) => (logs += data));
    app.stderr.on("data", (data) => (logs += data));
    for (let attempt = 0; ; attempt++) {
      try {
        await fetch(`${origin}/login`);
        break;
      } catch (error) {
        if (attempt >= 100) throw new Error(logs, { cause: error });
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    }
    const paths = [
      "/latest",
      "/news",
      "/topics",
      "/topics?offset=60",
      "/sources",
      `/topics/${topic.slug}`,
      `/topics/${topic.slug}/tutorials`,
      `/sources/${source.slug}`,
      `/sources/${source.slug}/tutorials`,
      `/tags/${topic.slug}`,
      `/articles/${article.slug}`,
      "/legal/terms",
      "/legal/privacy",
      "/mcp",
      "/users/promo-reader",
    ];
    const destination = `${root}/reports/structured-data`;
    await mkdir(destination, { recursive: true });
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const results = [];
    for (const [index, path] of paths.entries()) {
      let expected;
      for (const userAgent of ["Googlebot", "bingbot"]) {
        const response = await fetch(origin + path, { headers: { "User-Agent": userAgent } });
        assert.equal(response.status, 200, `${path}: ${logs}`);
        const html = await response.text();
        const dom = new JSDOM(html);
        const graphs = validateDocument(dom.window.document, path, article);
        if (!expected) expected = graphs;
        else assert.deepEqual(graphs, expected);
        if (userAgent === "Googlebot") await writeFile(`${destination}/${index}.html`, html);
        dom.window.close();
      }
      const response = await page.goto(origin + path);
      assert.equal(response.status(), 200);
      await page.waitForFunction(
        (count) => document.querySelectorAll('script[type="application/ld+json"]').length === count,
        expected.length,
      );
      const dom = new JSDOM(await page.content());
      assert.deepEqual(validateDocument(dom.window.document, path, article), expected);
      dom.window.close();
      results.push({
        path,
        canonical: await page.locator('link[rel="canonical"]').getAttribute("href"),
        graphs: expected.length,
      });
    }
    await writeFile(`${destination}/summary.json`, JSON.stringify(results, null, 2));
    console.log(
      `Validated crawler HTML and browser JSON-LD on ${paths.length} representative routes.`,
    );
  },
);

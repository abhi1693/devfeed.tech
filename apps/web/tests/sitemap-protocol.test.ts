import { readFileSync } from "node:fs";
import { afterEach, expect, it, vi } from "vitest";
import { XmlDocument, XsdValidator } from "libxml2-wasm";
import { sitemapDocument, sitemapIndex, sitemapPages, sitemapPart } from "@/lib/server/sitemaps";

function validate(xml: string, schema: "siteindex" | "sitemap") {
  const xsd = XmlDocument.fromString(
    readFileSync(new URL(`./fixtures/sitemaps/${schema}.xsd`, import.meta.url), "utf8"),
  );
  const document = XmlDocument.fromString(xml);
  const validator = XsdValidator.fromDoc(xsd);
  try {
    validator.validate(document);
  } finally {
    validator.dispose();
    document.dispose();
    xsd.dispose();
  }
}
const origin = "https://devfeed.tech";
const version = "a".repeat(32);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("validates the emitted index and URL sets against both official Sitemaps 0.9 schemas", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: URL) =>
      Promise.resolve(
        Response.json(
          url.pathname === "/v1/sitemaps"
            ? {
                generation: version,
                parts: [{ kind: "tags", page: 1 }],
              }
            : { paths: ["/tags/c%2B%2B", "/tags/a&b", "/tags/日本語"] },
        ),
      ),
    ),
  );
  const index = await sitemapIndex(new Request(origin + "/sitemap.xml"));
  const part = await sitemapPart(
    new Request(origin + "/sitemap-tags-1.xml?v=" + version),
    "tags",
    "1",
  );
  expect(index.status).toBe(200);
  expect(part.status).toBe(200);
  validate(await index.text(), "siteindex");
  const xml = await part.text();
  validate(xml, "sitemap");
  expect(xml).toContain("a&amp;b");
  expect(xml).toContain("%E6%97%A5%E6%9C%AC%E8%AA%9E");
  validate(await sitemapPages(new Request(origin + "/sitemap-pages.xml")).text(), "sitemap");
});

it("keeps an empty archive's index valid by including the homepage sitemap", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ generation: version, parts: [] })),
  );
  const response = await sitemapIndex(new Request(origin + "/sitemap.xml"));
  const xml = await response.text();
  expect(response.status).toBe(200);
  expect(xml).toContain(origin + "/sitemap-pages.xml");
  validate(xml, "siteindex");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ paths: [] })));
  const empty = await sitemapPart(new Request(origin + "/sitemap-articles-1.xml"), "articles", "1");
  expect(empty.status).toBe(404);
  expect(empty.headers.get("cache-control")).toBe("no-store");
  // Proves the validator catches the old empty-document behavior.
  expect(() =>
    validate('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', "sitemap"),
  ).toThrow();
});

it("rejects invalid URI references and measures the full percent-encoded URL", () => {
  for (const url of [
    origin + "/tags/bad%ZZ",
    origin + "/tags/a b",
    origin + "/tags/a\u0001",
    origin + "/tags/ok#fragment",
    "https://other.example/tags/x",
  ]) {
    expect(() => sitemapDocument("urlset", [url])).toThrow();
  }
  const longest = origin + "/" + "a".repeat(2047 - origin.length - 1);
  expect(() => sitemapDocument("urlset", [longest])).not.toThrow();
  expect(() => sitemapDocument("urlset", [longest + "a"])).toThrow(/URL/);
  expect(() => sitemapDocument("urlset", [origin + "/" + "語".repeat(300)])).toThrow(/URL/);
});

it("enforces both the 50,000-entry and 52,428,800-byte uncompressed limits", () => {
  expect(() => sitemapDocument("urlset", [])).toThrow(/count/);
  expect(() => sitemapDocument("sitemapindex", Array(50001).fill(origin + "/sitemap.xml"))).toThrow(
    /count/,
  );
  // Count and individual URL lengths are valid; only the total byte limit is exceeded.
  expect(() =>
    sitemapDocument("urlset", Array(50000).fill(origin + "/" + "a".repeat(1100))),
  ).toThrow(/size limit/);
}, 15000);

it("rejects traversal, empty slugs and malformed inventory instead of advertising invalid XML", async () => {
  for (const path of ["/tags/", "/tags/../settings", "/tags/%2E%2E", "/tags/x/y", "/tags/%ZZ"]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ paths: [path] })));
    expect(
      (await sitemapPart(new Request(origin + "/sitemap-tags-1.xml"), "tags", "1")).status,
    ).toBe(503);
  }
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        generation: version,
        parts: [
          { kind: "tags", page: 1 },
          { kind: "tags", page: 1 },
        ],
      }),
    ),
  );
  expect((await sitemapIndex(new Request(origin + "/sitemap.xml"))).status).toBe(503);
});

it("supports weak and strong If-None-Match comparison and bodyless 304 responses", () => {
  const initial = sitemapPages(new Request(origin + "/sitemap-pages.xml"));
  const etag = initial.headers.get("etag")!;
  expect(etag).toMatch(/^W\//);
  for (const value of [etag, etag.slice(2), `"unrelated", ${etag}`, "*"]) {
    const cached = sitemapPages(
      new Request(origin + "/sitemap-pages.xml", { headers: { "If-None-Match": value } }),
    );
    expect(cached.status).toBe(304);
    expect(cached.body).toBeNull();
    expect(cached.headers.get("etag")).toBe(etag);
    expect(cached.headers.get("cache-control")).toBe(initial.headers.get("cache-control"));
  }
  expect(
    sitemapPages(
      new Request(origin + "/sitemap-pages.xml", { headers: { "If-None-Match": '"stale"' } }),
    ).status,
  ).toBe(200);
});

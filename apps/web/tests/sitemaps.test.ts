import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { sitemapIndex, sitemapPart } from "@/lib/server/sitemaps";
import robots from "@/app/robots";
import { feedHref, parseFilters } from "@/lib/feed-query";

const version = "a".repeat(32);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("emits a same-origin, versioned sitemap index and conditional 304 responses", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const fetcher = vi.fn().mockImplementation(() =>
    Promise.resolve(
      Response.json({
        generation: version,
        parts: [
          { kind: "articles", page: 1 },
          { kind: "tags", page: 1 },
        ],
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const request = new Request("https://untrusted.test/sitemap.xml");
  const response = await sitemapIndex(request);
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toContain("s-maxage=60");
  const xml = await response.text();
  expect(xml).toContain('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">');
  expect(xml).toContain(`https://devfeed.tech/sitemap-tags-1.xml?v=${version}`);
  expect(xml).not.toContain("untrusted.test");
  const cached = await sitemapIndex(
    new Request(request, { headers: { "If-None-Match": response.headers.get("etag")! } }),
  );
  expect(cached.status).toBe(304);
  expect(await cached.text()).toBe("");
});

it("escapes XML and requests only the specified stored shard", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ paths: ["/tags/c%2B%2B", "/tags/a&b"] }));
  vi.stubGlobal("fetch", fetcher);
  const result = await sitemapPart(
    new Request(`https://devfeed.tech/sitemap-tags-1.xml?v=${version}`),
    "tags",
    "1",
  );
  const xml = await result.text();
  expect(xml).toContain("<urlset xmlns=");
  expect(xml).toContain("https://devfeed.tech/tags/c%2B%2B");
  expect(xml).toContain("https://devfeed.tech/tags/a&amp;b");
  expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/sitemaps/tags/1");
  expect(fetcher.mock.calls[0][0].search).toBe(`?v=${version}`);
  expect(xml).not.toContain("lastmod");
});

it("does not cache outages or publish empty XML when the inventory fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })));
  const result = await sitemapIndex(new Request("https://devfeed.tech/sitemap.xml"));
  expect(result.status).toBe(503);
  expect(result.headers.get("cache-control")).toBe("no-store");
  expect(result.headers.get("retry-after")).toBe("5");
});

it("rejects invalid shard paths before contacting the backend", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const request = new Request("https://devfeed.tech/sitemap-users-1.xml");
  expect((await sitemapPart(request, "users", "1")).status).toBe(404);
  expect((await sitemapPart(request, "articles", "0")).status).toBe(404);
  expect((await sitemapPart(new Request(request.url + "?v=bad"), "articles", "1")).status).toBe(
    404,
  );
  expect(fetcher).not.toHaveBeenCalled();
});

it("advertises the index in robots and gives tags indexable canonical routes", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  expect(robots().sitemap).toBe("https://devfeed.tech/sitemap.xml");
  expect(feedHref(parseFilters({ tag: "c++" }))).toBe("/tags/c%2B%2B");
  expect(feedHref(parseFilters({ tag: "python", content_type: "tutorial" }))).toBe(
    "/tags/python/tutorials",
  );
});

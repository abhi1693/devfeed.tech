import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { sitemapIndex, sitemapPages, sitemapPart } from "@/lib/server/sitemaps";
import robots from "@/app/robots";
import { feedHref, parseFilters } from "@/lib/feed-query";
import {
  matchReaderDestinationPath,
  readerDestinationContract,
  serializeReaderDestination,
  serializeReaderLoginDestination,
} from "@devfeed/http/reader-destinations";
import { legalDocument } from "@/lib/legal";

vi.mock("@/components/legal-page", () => ({
  LegalContact: () => null,
  LegalPage: () => null,
}));

const version = "a".repeat(32);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("publishes the privacy update without changing the terms modification date", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ latest_publication: {} })));
  const response = await sitemapPages(new Request("https://devfeed.tech/sitemap-pages.xml"));
  expect(response.status).toBe(200);
  const xml = await response.text();
  for (const [path, date] of [
    ["/legal/privacy", "2026-09-14"],
    ["/legal/terms", "2026-09-13"],
  ]) {
    const entry = xml
      .split("<url>")
      .find((value) => value.includes(`<loc>https://devfeed.tech${path}</loc>`));
    expect(entry).toContain(`<lastmod>${date}T00:00:00.000Z</lastmod>`);
  }
});

it("uses the legal document's metadata for the page, navigation and sitemap", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const { default: PrivacyPage, generateMetadata } = await import("@/app/legal/privacy/page");
  const document = legalDocument("legal-privacy");
  expect(PrivacyPage().props.document).toBe(document);
  expect(generateMetadata()).toMatchObject({
    title: document.title,
    description: document.description,
    alternates: { canonical: "https://devfeed.tech" + document.path },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ latest_publication: {} })));
  const xml = await (
    await sitemapPages(new Request("https://devfeed.tech/sitemap-pages.xml"))
  ).text();
  const entry = xml.split("<url>").find((value) => value.includes(document.path));
  expect(entry).toContain(`<lastmod>${document.updated}T00:00:00.000Z</lastmod>`);
});

it("keeps sitemap policy and publication identity attached when a navigation route changes", async () => {
  const definition = readerDestinationContract.routes.find((route) => route.id === "latest")!;
  const segments = definition.segments as string[];
  const original = segments[0];
  segments[0] = "fresh";
  try {
    expect(matchReaderDestinationPath("/fresh")?.definition.id).toBe("latest");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ latest_publication: { articles: "2026-09-12T12:00:00Z" } }),
        ),
    );
    const xml = await (
      await sitemapPages(new Request("https://devfeed.tech/sitemap-pages.xml"))
    ).text();
    const entry = xml.split("<url>").find((value) => value.includes("/fresh</loc>"));
    expect(entry).toContain("<lastmod>2026-09-12T12:00:00.000Z</lastmod>");
    expect(entry).toContain("<changefreq>hourly</changefreq>");
    expect(entry).toContain("<priority>1.0</priority>");
    expect(xml).not.toContain("/latest</loc>");
  } finally {
    segments[0] = original;
  }
});

it("publishes declared pages independently of their sign-in destination eligibility", async () => {
  const definition = readerDestinationContract.routes.find((route) => route.id === "latest")!;
  const original = definition.login;
  definition.login = false;
  try {
    const destination = { routeId: "latest", parameters: {}, query: {} };
    expect(serializeReaderDestination(destination)).toBe("/latest");
    expect(serializeReaderLoginDestination(destination)).toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ latest_publication: {} })));
    const response = await sitemapPages(new Request("https://devfeed.tech/sitemap-pages.xml"));
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("https://devfeed.tech/latest</loc>");
  } finally {
    definition.login = original;
  }
});

it("selects static-page publication keys and hints from declared metadata", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        latest_publication: {
          articles: "2026-09-10T12:00:00Z",
          "feed:article": "2026-09-11T12:00:00Z",
          "feed:tutorial": "2026-09-12T12:00:00Z",
          topics: "2026-09-13T12:00:00Z",
          sources: "2026-09-14T12:00:00Z",
        },
      }),
    ),
  );
  const xml = await (
    await sitemapPages(new Request("https://devfeed.tech/sitemap-pages.xml"))
  ).text();
  for (const [path, date, frequency, priority] of [
    ["/latest", "2026-09-10", "hourly", "1.0"],
    ["/articles", "2026-09-11", "hourly", "0.7"],
    ["/tutorials", "2026-09-12", "hourly", "0.7"],
    ["/topics", "2026-09-13", "daily", "0.7"],
    ["/sources", "2026-09-14", "daily", "0.7"],
  ]) {
    const entry = xml.split("<url>").find((value) => value.includes(`${path}</loc>`));
    expect(entry).toContain(`<lastmod>${date}T12:00:00.000Z</lastmod>`);
    expect(entry).toContain(`<changefreq>${frequency}</changefreq>`);
    expect(entry).toContain(`<priority>${priority}</priority>`);
  }
  expect(xml).not.toContain("https://devfeed.tech/</loc>");
  expect(xml).not.toContain("/settings");
});

it("fetches fresh inventory with a bounded public client without forwarding reader credentials", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "https://public-api.example");
  const deadline = vi.spyOn(AbortSignal, "timeout");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ latest_publication: {} }));
  vi.stubGlobal("fetch", fetcher);
  const response = await sitemapPages(
    new Request("https://devfeed.tech/sitemap-pages.xml", {
      headers: { Cookie: "devfeed_session=private", Authorization: "Bearer private" },
    }),
  );
  expect(response.status).toBe(200);
  expect(deadline).toHaveBeenCalledWith(55_000);
  const [url, options] = fetcher.mock.calls[0];
  expect(url.href).toBe("https://public-api.example/v1/sitemaps");
  expect(options).toMatchObject({ cache: "no-store", redirect: "error" });
  const headers = new Headers(options.headers);
  expect(headers.get("cache-control")).toBe("no-store");
  expect(headers.get("cookie")).toBeNull();
  expect(headers.get("authorization")).toBeNull();
});

it.each([
  ["articles", "monthly", "0.8"],
  ["topics", "daily", "0.6"],
  ["tags", "daily", "0.6"],
  ["sources", "daily", "0.6"],
])("uses the %s inventory's declared hints", async (kind, frequency, priority) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ paths: [`/${kind}/sample`] })));
  const response = await sitemapPart(
    new Request(`https://devfeed.tech/sitemap-${kind}-1.xml`),
    kind,
    "1",
  );
  expect(response.status).toBe(200);
  const xml = await response.text();
  expect(xml).toContain(`<changefreq>${frequency}</changefreq>`);
  expect(xml).toContain(`<priority>${priority}</priority>`);
});

it("validates inventory paths using the route definition independently of the inventory name", async () => {
  const definition = readerDestinationContract.routes.find((route) => route.id === "article")!;
  const segments = definition.segments as (typeof definition.segments)[number][];
  const original = segments[0];
  segments[0] = "stories";
  try {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ paths: ["/stories/sample"] }));
    vi.stubGlobal("fetch", fetcher);
    const response = await sitemapPart(
      new Request("https://devfeed.tech/sitemap-articles-1.xml"),
      "articles",
      "1",
    );
    expect(response.status).toBe(200);
    expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/sitemaps/articles/1");
    const xml = await response.text();
    expect(xml).toContain("https://devfeed.tech/stories/sample");
    expect(xml).toContain("<changefreq>monthly</changefreq>");
    expect(xml).toContain("<priority>0.8</priority>");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(Response.json({ paths: ["/articles/sample"] })),
    );
    expect(
      (
        await sitemapPart(
          new Request("https://devfeed.tech/sitemap-articles-1.xml"),
          "articles",
          "1",
        )
      ).status,
    ).toBe(503);
  } finally {
    segments[0] = original;
  }
});

it("uses only the declared pages' publication sources to modify their sitemap index entry", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        generation: version,
        parts: [],
        latest_publication: { private_account: "not-a-date" },
      }),
    ),
  );
  expect((await sitemapIndex(new Request("https://devfeed.tech/sitemap.xml"))).status).toBe(200);
});

it("emits a same-origin, stable sitemap index and conditional 304 responses", async () => {
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
  expect(xml).toContain(`https://devfeed.tech/sitemap-tags-1.xml`);
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
    new Request(`https://devfeed.tech/sitemap-tags-1.xml`),
    "tags",
    "1",
  );
  const xml = await result.text();
  expect(xml).toContain("<urlset xmlns=");
  expect(xml).toContain("https://devfeed.tech/tags/c%2B%2B");
  expect(xml).toContain("https://devfeed.tech/tags/a&amp;b");
  expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/sitemaps/tags/1");
  expect(fetcher.mock.calls[0][0].search).toBe("");
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
    308,
  );
  expect(fetcher).not.toHaveBeenCalled();
});

it("advertises the index in robots and gives tags indexable canonical routes", () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  expect(robots().sitemap).toBe("https://devfeed.tech/sitemap.xml");
  expect(robots().rules).toEqual({ userAgent: "*", allow: "/", disallow: ["/api/"] });
  expect(feedHref(parseFilters({ tag: "c++" }))).toBe("/tags/c%2B%2B");
  expect(feedHref(parseFilters({ tag: "python", content_type: "tutorial" }))).toBe(
    "/tags/python/tutorials",
  );
});

it("redirects all legacy snapshot IDs, including truncated IDs, to clean URLs", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const id of [version, version.slice(0, -1), "expired", ""]) {
    const response = await sitemapPart(
      new Request(`https://devfeed.tech/sitemap-articles-1.xml?v=${id}`),
      "articles",
      "1",
    );
    expect(response.status).toBe(308);
    expect(response.headers.get("location")).toBe("https://devfeed.tech/sitemap-articles-1.xml");
  }
  expect(fetcher).not.toHaveBeenCalled();
});

it("emits persisted sitemap modification dates and page hints on stable URLs", async () => {
  const modifiedAt = "2026-09-12T12:00:00.000Z";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        generation: version,
        parts: [{ kind: "articles", page: 1, modified_at: Date.parse(modifiedAt) / 1000 }],
      }),
    ),
  );
  const index = await (await sitemapIndex(new Request("https://devfeed.tech/sitemap.xml"))).text();
  expect(index).toContain(`<lastmod>${modifiedAt}</lastmod>`);
  expect(index).not.toContain("?v=");
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      paths: ["/articles/example"],
      entries: [{ path: "/articles/example", lastmod: "2026-09-12T12:00:00+00:00" }],
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const xml = await (
    await sitemapPart(new Request("https://devfeed.tech/sitemap-articles-1.xml"), "articles", "1")
  ).text();
  expect(fetcher.mock.calls[0][0].search).toBe("");
  expect(xml).toContain("<changefreq>monthly</changefreq>");
  expect(xml).toContain("<priority>0.8</priority>");
  expect(xml).toContain(`<lastmod>${modifiedAt}</lastmod>`);
});

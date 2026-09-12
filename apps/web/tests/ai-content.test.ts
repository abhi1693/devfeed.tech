import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";
import { publicMarkdown } from "@/lib/server/ai-content";
import { llmsFull, llmsIndex } from "@/lib/server/llms";
import type { Article } from "@/lib/types";

vi.mock("next/headers", () => ({
  cookies: () => {
    throw new Error("Must not read account cookies");
  },
}));
const article: Article = {
  id: "article-id",
  slug: "understanding-cpp",
  title: "Understanding C++ [guide]",
  canonical_url: "https://publisher.example/posts/cpp",
  summary: "The publisher excerpt.",
  ai_summary: "A **generated** overview.",
  ai_description: null,
  image_url: null,
  author: "An author",
  content_type: "tutorial",
  content_format: null,
  language: "en",
  published_at: "2026-09-12T10:00:00Z",
  feed_at: "2026-09-12T10:10:00Z",
  tags: ["c++"],
  topics: [{ id: "topic-id", name: "C++", slug: "c++", kind: "language" }],
  sources: [
    {
      id: "12345678-1234-1234-1234-123456789012",
      name: "Publisher",
      website_url: "https://publisher.example",
      logo_url: null,
      description: null,
    },
  ],
};
const req = (path: string, headers?: HeadersInit) =>
  new Request(`https://untrusted.example${path}`, { headers });
const run = (path: string[], query = "", headers?: HeadersInit) =>
  publicMarkdown(req(`/md/${path.join("/")}${query}`, headers), path);
beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://devfeed.tech");
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api.internal:8000");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("uses Dualmark for explicit Markdown, AI bot negotiation and HTML discovery", async () => {
  for (const [path, headers, expected] of [
    ["/articles/understanding-cpp", { Accept: "text/markdown" }, true],
    ["/articles/understanding-cpp.md", { Accept: "text/html" }, true],
    ["/articles/understanding-cpp", { "User-Agent": "GPTBot", Accept: "*/*" }, true],
    ["/articles/understanding-cpp", { "User-Agent": "GPTBot", Accept: "text/html" }, false],
    ["/articles/understanding-cpp", { Accept: "text/markdown;q=0, text/html;q=1" }, false],
  ] as const) {
    const response = await proxy(new NextRequest(`https://devfeed.tech${path}`, { headers }));
    expect(response.headers.has("x-middleware-rewrite")).toBe(expected);
    expect(response.headers.get("vary")).toContain("Accept");
    expect(response.headers.get("vary")).toContain("User-Agent");
    if (expected)
      expect(response.headers.get("x-middleware-rewrite")).toBe(
        "https://devfeed.tech/md/articles/understanding-cpp",
      );
    else
      expect(response.headers.get("link")).toContain(
        "https://devfeed.tech/articles/understanding-cpp.md",
      );
  }
  const html = await proxy(
    new NextRequest("https://untrusted.example/?tag=c%2B%2B", { headers: { Accept: "text/html" } }),
  );
  expect(html.headers.get("link")).toContain("https://devfeed.tech/index.md?tag=c%2B%2B");
  expect(html.headers.get("cache-control")).toBe("private, no-store");
  const unsupported = await proxy(
    new NextRequest("https://devfeed.tech/topics", { headers: { Accept: "application/json" } }),
  );
  expect(unsupported.status).toBe(406);
});

it("leaves private routes, assets, XML, discovery files, React navigation and actions alone", async () => {
  for (const path of [
    "/api/v1/user/me",
    "/settings",
    "/my-feed",
    "/sources/suggest",
    "/sources/suggest.md",
    "/login",
    "/register",
    "/llms.txt",
    "/llms-full.txt",
    "/robots.txt",
    "/sitemap.xml",
    "/logo.png",
    "/unknown",
    "/md/settings",
  ]) {
    const response = await proxy(
      new NextRequest(`https://devfeed.tech${path}`, {
        headers: { Accept: "text/markdown", "User-Agent": "GPTBot" },
      }),
    );
    expect(response.headers.has("x-middleware-rewrite")).toBe(false);
    expect(response.headers.has("link")).toBe(false);
  }
  for (const key of ["rsc", "next-action", "next-router-prefetch"]) {
    const response = await proxy(
      new NextRequest("https://devfeed.tech/topics", {
        headers: { [key]: "1", Accept: "text/markdown" },
      }),
    );
    expect(response.headers.has("x-middleware-rewrite")).toBe(false);
  }
  const action = await proxy(
    new NextRequest("https://devfeed.tech/topics", {
      method: "POST",
      headers: { Accept: "text/markdown" },
    }),
  );
  expect(action.headers.has("x-middleware-rewrite")).toBe(false);
});

it("renders one public article lookup with attribution, typed headers and no account data", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(article)));
  vi.stubGlobal("fetch", fetcher);
  const response = await run(["articles", article.slug], "", {
    Cookie: "devfeed_user_session=secret",
    Authorization: "Bearer secret",
  });
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
  expect(response.headers.get("x-markdown-tokens")).toMatch(/^\d+$/);
  expect(response.headers.get("x-aeo-version")).toBe("1.0");
  expect(response.headers.get("vary")).toBe("Accept, User-Agent");
  expect(response.headers.get("cache-control")).toBe("public, max-age=0, must-revalidate");
  expect(response.headers.get("link")).toContain(`https://devfeed.tech/articles/${article.slug}`);
  const body = await response.text();
  expect(body).toContain("# Understanding C++ \\[guide\\]");
  expect(body).toContain("https://publisher.example/posts/cpp");
  expect(body).toContain("## AI overview\n\nA **generated** overview.");
  expect(body).toContain("## Source excerpt\n\nThe publisher excerpt.");
  expect(body).toContain("https://devfeed.tech/tags/c%2B%2B.md");
  expect(body).not.toMatch(/untrusted|secret/);
  expect(fetcher).toHaveBeenCalledOnce();
  const [url, options] = fetcher.mock.calls[0];
  expect(url.href).toBe(`http://public-api.internal:8000/v1/articles/${article.slug}`);
  expect(options.headers).toEqual({ Accept: "application/json" });
  const conditional = await run(["articles", article.slug], "", {
    "If-None-Match": `W/${response.headers.get("etag")}`,
  });
  expect(conditional.status).toBe(304);
  expect(await conditional.text()).toBe("");
});

it("passes decoded tag slugs once and keeps filters in cursor pagination", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((url: URL) =>
      Promise.resolve(
        Response.json(
          url.pathname.startsWith("/v1/tags/")
            ? { id: "tag", name: "C++", slug: "c++" }
            : { items: [article], next_cursor: "opaque+/cursor=" },
        ),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  const response = await run(["tags", "c++", "tutorials"], "?language=en&cursor=previous");
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/tags/c%2B%2B");
  const feedUrl = fetcher.mock.calls[1][0] as URL;
  expect(feedUrl.pathname).toBe("/v1/feed");
  expect(Object.fromEntries(feedUrl.searchParams)).toEqual({
    tag: "c++",
    content_type: "tutorial",
    language: "en",
    cursor: "previous",
    limit: "24",
  });
  const body = await response.text();
  expect(body).toContain("/tags/c%2B%2B/tutorials.md?");
  expect(body).toContain("cursor=opaque%2B%2Fcursor%3D");
  expect(body).toContain("language=en");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("bounds directory reads and lists only entries with published articles", async () => {
  const fetcher = vi.fn().mockImplementation(() =>
    Promise.resolve(
      Response.json(
        Array.from({ length: 60 }, (_, i) => ({
          id: `id-${i}`,
          slug: `topic-${i}`,
          name: `Topic ${i}`,
          description: "Description",
        })),
      ),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  const response = await run(["topics"], "?offset=60");
  const url = fetcher.mock.calls[0][0] as URL;
  expect(url.pathname).toBe("/v1/topics");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    offset: "60",
    limit: "60",
    has_articles: "true",
  });
  expect(await response.text()).toContain("https://devfeed.tech/topics.md?offset=120");
  expect(fetcher).toHaveBeenCalledOnce();
});

it("renders search sections with articles first and per-section continuation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Promise.resolve(
        Response.json({
          query: "cpp",
          sections: {
            tags: {
              items: [{ title: "C++", description: "Tag", href: "/tags/c%2B%2B" }],
              next_cursor: null,
            },
            articles: {
              items: [
                { title: "Guide", description: "Learn C++", href: `/articles/${article.slug}` },
              ],
              next_cursor: "2",
            },
          },
        }),
      ),
    ),
  );
  const body = await (await run(["search"], "?q=cpp")).text();
  expect(body.indexOf("## articles")).toBeLessThan(body.indexOf("## tags"));
  expect(body).toContain("/search.md?q=cpp&section=articles&page=2");
  expect(body).toContain(`/articles/${article.slug}.md`);
});

it("rejects private paths and malformed directory parameters without backend calls", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  for (const path of [
    ["settings"],
    ["api", "v1", "user"],
    ["sources", "suggest"],
    ["topics", "../secret"],
    ["tags", "bad?slug"],
  ]) {
    expect((await run(path)).status).toBe(404);
  }
  expect((await run(["topics"], "?offset=-1")).status).toBe(400);
  expect((await run(["topics"], "?offset=1000001")).status).toBe(400);
  expect((await run(["search"], "?q=cpp&section=users")).status).toBe(400);
  expect(fetcher).not.toHaveBeenCalled();
});

it("returns missing publications and failures without caching error bodies", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("private backend detail", { status: 404 })),
  );
  const missing = await run(["articles", article.slug]);
  expect(missing.status).toBe(404);
  expect(missing.headers.get("cache-control")).toBe("no-store");
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private database address")));
  const outage = await run(["index"]);
  expect(outage.status).toBe(503);
  expect(outage.headers.get("retry-after")).toBe("5");
  expect(await outage.text()).not.toContain("private");
});

it("serves a DB-free llms index with configured public links and ETag support", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const response = await llmsIndex(req("/llms.txt"));
  const body = await response.text();
  expect(body).toContain("# DevFeed");
  expect(body).toContain("https://devfeed.tech/llms-full.txt");
  expect(body).toContain("https://devfeed.tech/sitemap.xml");
  expect(body).not.toContain("untrusted.example");
  expect(response.headers.get("content-type")).toBe("text/plain; charset=utf-8");
  expect(response.headers.get("cache-control")).toContain("max-age=3600");
  expect(
    (await llmsIndex(req("/llms.txt", { "If-None-Match": response.headers.get("etag")! }))).status,
  ).toBe(304);
  expect(fetcher).not.toHaveBeenCalled();
});

it("expands the full guide using exactly four bounded, anonymous public API reads", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((url: URL) =>
      Promise.resolve(
        Response.json(url.pathname === "/v1/feed" ? { items: [article], next_cursor: "next" } : []),
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  const response = await llmsFull(req("/llms-full.txt", { Cookie: "devfeed_user_session=secret" }));
  expect(response.status).toBe(200);
  const body = await response.text();
  expect(body).toContain("Full agent guide");
  expect(body).toContain("not a dump of the entire article archive");
  expect(body).toContain("The publisher excerpt.");
  expect(body).toContain("Next page");
  expect(body).toContain("# DevFeed tags");
  expect(fetcher).toHaveBeenCalledTimes(4);
  for (const [url, options] of fetcher.mock.calls) {
    expect(url.origin).toBe("http://public-api.internal:8000");
    expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(60);
    expect(options.headers).toEqual({ Accept: "application/json" });
  }
});

it("matches HTML canonicals for Markdown aliases and removes tracking parameters", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(article)));
  vi.stubGlobal("fetch", fetcher);
  const alias = await run(["articles", "legacy-id"], "?utm_source=agent");
  expect(alias.headers.get("link")).toBe(
    `<https://devfeed.tech/articles/${article.slug}>; rel="canonical"`,
  );
  expect(fetcher).toHaveBeenCalledOnce();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: null })),
  );
  const feed = await run(["index"], "?tag=c%2B%2B&cursor=next&utm_source=agent");
  expect(feed.headers.get("link")).toBe(
    '<https://devfeed.tech/tags/c%2B%2B?cursor=next>; rel="canonical"',
  );
});

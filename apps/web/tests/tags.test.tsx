import { proxy } from "@/proxy";
import { NextRequest } from "next/server";
import { afterEach, expect, it, vi } from "vitest";
import { GET as list } from "@/app/api/v1/tags/route";
import { GET as detail } from "@/app/api/v1/tags/[slug]/route";
import { resolveReaderRoute, readerRouteLabels } from "@/lib/reader-routes";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("keeps tag directory searches inside the extension reader", () => {
  expect(resolveReaderRoute("/tags")).toEqual({ type: "local", page: "catalog", catalog: "tags" });
  for (const slug of ["c++", "c#", ".net", "数据库", "two words"]) {
    const route = resolveReaderRoute(`/tags/${encodeURIComponent(slug)}/tutorials`);
    expect(route).toMatchObject({ detail: { kind: "tags", slug, contentType: "tutorial" } });
    expect(readerRouteLabels(route)).toEqual({
      telemetry: "/tags/:id/:type",
      screen: "/tags/detail",
    });
  }
});
it("bounds catalog pages and forwards search and alphabet filters without credentials", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const items = Array.from({ length: 61 }, (_, i) => ({
    id: String(i),
    name: `C ${i}`,
    slug: `c-${i}`,
  }));
  const fetcher = vi.fn().mockResolvedValue(Response.json(items));
  vi.stubGlobal("fetch", fetcher);
  const response = await list(
    new Request("https://devfeed.test/api/v1/tags?q=C%2B%2B&letter=C&offset=60&limit=9999", {
      headers: { authorization: "Bearer secret", cookie: "secret=value" },
    }),
  );
  expect(await response.json()).toEqual({ items: items.slice(0, 60), next_cursor: "120" });
  const [url, options] = fetcher.mock.calls[0];
  expect(url.pathname).toBe("/v1/tags");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    limit: "61",
    offset: "60",
    q: "C++",
    letter: "C",
  });
  expect(options.headers).not.toHaveProperty("authorization");
  expect(options.headers).not.toHaveProperty("cookie");
});
it("preserves punctuation in tag detail requests and rejects unsafe paths", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ id: "cpp", name: "C++", slug: "c++" }));
  vi.stubGlobal("fetch", fetcher);
  const response = await detail(new Request("https://devfeed.test/api/v1/tags/c%2B%2B"), {
    params: Promise.resolve({ slug: "c++" }),
  });
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/tags/c%2B%2B");
  expect(
    (
      await detail(new Request("https://devfeed.test/"), {
        params: Promise.resolve({ slug: "../private" }),
      })
    ).status,
  ).toBe(404);
});
it("returns a recoverable error when tag loading fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  expect((await list(new Request("https://devfeed.test/api/v1/tags"))).status).toBe(503);
});

it.each([404, 503])(
  "preserves tag detail upstream status %i without caching the error",
  async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({}, { status })));
    const response = await detail(new Request("https://devfeed.test/api/v1/tags/missing"), {
      params: Promise.resolve({ slug: "missing" }),
    });
    expect(response.status).toBe(status);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ detail: "Couldn’t load tag" });
  },
);

it("returns a recoverable uncached detail error for an unavailable upstream", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  const response = await detail(new Request("https://devfeed.test/api/v1/tags/rust"), {
    params: Promise.resolve({ slug: "rust" }),
  });
  expect(response.status).toBe(503);
  expect(response.headers.get("cache-control")).toBe("no-store");
});

it("overwrites supplied reader path headers with the actual request path", async () => {
  const response = await proxy(
    new NextRequest("https://devfeed.tech/tags/c%2B%2B", {
      headers: { "x-devfeed-reader-pathname": "/tags/wrong", accept: "text/html" },
    }),
  );
  expect(response.headers.get("x-middleware-request-x-devfeed-reader-pathname")).toBe(
    "/tags/c%2B%2B",
  );
});

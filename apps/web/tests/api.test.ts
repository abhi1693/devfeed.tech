import { afterEach, expect, it, vi } from "vitest";
import { getArticle, getFeed, getTopic, getTopics, getSources, UserApiError } from "@/lib/api";
import { parseFilters } from "@/lib/feed-query";
vi.mock("next/headers", () => ({ cookies: async () => ({ toString: () => "" }) }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("uses only the configured public API and preserves encoded filters", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://api.internal:8000");
  const fetch = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetch);
  expect(await getFeed(parseFilters({ q: "C++ & APIs", topic: "cpp" }))).toEqual({
    items: [],
    next_cursor: null,
  });
  const [url, options] = fetch.mock.calls[0];
  expect(url.origin).toBe("http://api.internal:8000");
  expect(url.searchParams.get("q")).toBe("C++ & APIs");
  expect(url.searchParams.get("limit")).toBe("24");
  expect(options.cache).toBe("no-store");
  expect(options.headers).toEqual({ Accept: "application/json" });
});
it("preserves a missing article response for page-level 404 handling", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
  await expect(getArticle("id")).rejects.toMatchObject({ status: 404 });
});
it("reports network outages separately from empty feeds", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("internal secret address")));
  await expect(getFeed(parseFilters({}))).rejects.toEqual(new UserApiError(503));
});
it("treats invalid JSON as an upstream error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("invalid json")));
  await expect(getTopic("typescript")).rejects.toMatchObject({ status: 502 });
});

it("requests only topics with visible articles before applying page bounds", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal("fetch", fetch);
  await getTopics(60, 60);
  const url = fetch.mock.calls[0][0];
  expect(url.pathname).toBe("/v1/topics");
  expect(url.searchParams.get("has_articles")).toBe("true");
  expect(url.searchParams.get("offset")).toBe("60");
  expect(url.searchParams.get("limit")).toBe("60");
  expect(fetch).toHaveBeenCalledOnce();
});

it("requests only enabled sources with visible articles for directories and settings", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal("fetch", fetch);
  await getSources(60, 60);
  const url = fetch.mock.calls[0][0];
  expect(url.pathname).toBe("/v1/sources");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    limit: "60",
    offset: "60",
    enabled: "true",
    has_articles: "true",
  });
});

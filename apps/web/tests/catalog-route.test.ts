import { afterEach, expect, it, vi } from "vitest";
import { GET as topics } from "@/app/api/v1/topics/route";
import { GET as sources } from "@/app/api/v1/sources/route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
it.each([["topics", topics, "has_articles"], ["sources", sources, "enabled"]] as const)("serves bounded %s pages with fixed public filters and no credentials", async (kind, get, filter) => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const items = Array.from({ length: 60 }, (_, i) => ({ id: String(i) }));
  const fetcher = vi.fn().mockResolvedValue(Response.json(items));
  vi.stubGlobal("fetch", fetcher);
  const response = await get(new Request(`https://devfeed.test/api/v1/${kind}?offset=60&limit=999999&enabled=false&has_articles=false&url=https://evil.example`, { headers: { cookie: "secret=value", authorization: "Bearer private" } }));
  expect(await response.json()).toEqual({ items, next_cursor: "120" });
  expect(response.headers.get("cache-control")).toBe("no-store");
  const [url, options] = fetcher.mock.calls[0];
  expect(url.origin).toBe("http://public-api:8000");
  expect(url.pathname).toBe(`/v1/${kind}`);
  expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "60", offset: "60", [filter]: "true", has_articles: "true" });
  expect(options.headers).toEqual({ Accept: "application/json" });
});
it("bounds offsets, stops on a short batch and cancels the upstream request", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json([])); vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const response = await topics(new Request("https://devfeed.test/api/v1/topics?offset=999999999999", { signal: controller.signal }));
  expect(fetcher.mock.calls[0][0].searchParams.get("offset")).toBe("1000000");
  expect(await response.json()).toEqual({ items: [], next_cursor: null });
  controller.abort(); expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});
it("hides network details on failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private host")));
  const response = await sources(new Request("https://devfeed.test/api/v1/sources"));
  expect(response.status).toBe(503); expect(await response.text()).not.toContain("private host");
});

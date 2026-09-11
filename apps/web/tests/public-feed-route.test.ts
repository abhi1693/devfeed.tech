import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/feed/route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
it("serves bounded public batches without forwarding credentials or arbitrary upstream paths", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: "next" }));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(new Request("https://devfeed.test/api/v1/feed?content_type=news&q=Python&cursor=a%2Bb%3D&limit=100000&url=https://other.test", { headers: { cookie: "secret=session", authorization: "Bearer private" } }));
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.json()).toEqual({ items: [], next_cursor: "next" });
  const [url, options] = fetcher.mock.calls[0];
  expect(url.origin).toBe("http://public-api:8000");
  expect(url.pathname).toBe("/v1/feed");
  expect(Object.fromEntries(url.searchParams)).toEqual({ q: "Python", content_type: "news", cursor: "a+b=", limit: "24" });
  expect(options.headers).toEqual({ Accept: "application/json" });
});
it("preserves cursor errors and hides upstream network details", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({}, { status: 422 })).mockRejectedValueOnce(new Error("private backend address"));
  vi.stubGlobal("fetch", fetcher);
  expect((await GET(new Request("https://devfeed.test/api/v1/feed?cursor=bad"))).status).toBe(422);
  const response = await GET(new Request("https://devfeed.test/api/v1/feed"));
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("private backend");
});
it("propagates cancellation to the upstream feed request", async () => {
  const controller = new AbortController();
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetcher);
  await GET(new Request("https://devfeed.test/api/v1/feed", { signal: controller.signal }));
  controller.abort();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

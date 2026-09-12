import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/feed/route";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("serves bounded public batches without forwarding credentials or arbitrary upstream paths", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: "next" }));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request(
      "https://devfeed.test/api/v1/feed?content_type=news&q=Python&cursor=a%2Bb%3D&limit=100000&url=https://other.test",
      { headers: { cookie: "secret=session", authorization: "Bearer private" } },
    ),
  );
  expect(response.status).toBe(200);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(await response.json()).toEqual({ items: [], next_cursor: "next" });
  const [url, options] = fetcher.mock.calls[0];
  expect(url.origin).toBe("http://public-api:8000");
  expect(url.pathname).toBe("/v1/feed");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    q: "Python",
    content_type: "news",
    cursor: "a+b=",
    limit: "24",
  });
  expect(options.headers).toEqual({ Accept: "application/json" });
});
it("preserves cursor errors and hides upstream network details", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({}, { status: 422 }))
    .mockRejectedValueOnce(new Error("private backend address"));
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
it("resolves account content preferences without leaking credentials to the public API", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8000");
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ view: "cards", content_types: ["news", "tutorial"] }))
    .mockResolvedValueOnce(Response.json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request("https://devfeed.test/api/v1/feed?cursor=next", {
      headers: { cookie: "admin_session=private; devfeed_user_session=user-session" },
    }),
  );
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls[0][0].origin).toBe("http://user-api:8000");
  expect(fetcher.mock.calls[0][1].headers.Cookie).toBe("devfeed_user_session=user-session");
  expect(fetcher.mock.calls[1][0].searchParams.getAll("content_types")).toEqual([
    "news",
    "tutorial",
  ]);
  expect(fetcher.mock.calls[1][0].searchParams.get("cursor")).toBe("next");
  expect(fetcher.mock.calls[1][1].headers).toEqual({ Accept: "application/json" });
});
it("honors explicit type tabs without loading private defaults", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetcher);
  await GET(
    new Request("https://devfeed.test/api/v1/feed?content_type=opinion", {
      headers: { cookie: "devfeed_user_session=test" },
    }),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0].searchParams.get("content_type")).toBe("opinion");
});
it("does not silently ignore preferences when their service fails", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8000");
  const fetcher = vi.fn().mockResolvedValue(Response.json({}, { status: 503 }));
  vi.stubGlobal("fetch", fetcher);
  expect(
    (
      await GET(
        new Request("https://devfeed.test/api/v1/feed", {
          headers: { cookie: "devfeed_user_session=test" },
        }),
      )
    ).status,
  ).toBe(503);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

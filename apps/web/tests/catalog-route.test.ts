import { afterEach, expect, it, vi } from "vitest";
import { GET as topics } from "@/app/api/v1/topics/route";
import { GET as sources } from "@/app/api/v1/sources/route";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it.each([
  ["topics", topics, "has_articles"],
  ["sources", sources, "enabled"],
] as const)(
  "serves bounded %s pages with fixed public filters and no credentials",
  async (kind, get, filter) => {
    vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
    const items = Array.from({ length: 60 }, (_, i) => ({ id: String(i) }));
    const fetcher = vi.fn().mockResolvedValue(Response.json(items));
    vi.stubGlobal("fetch", fetcher);
    const response = await get(
      new Request(
        `https://devfeed.test/api/v1/${kind}?offset=60&limit=999999&enabled=false&has_articles=false&url=https://evil.example`,
        { headers: { cookie: "secret=value", authorization: "Bearer private" } },
      ),
    );
    expect(await response.json()).toEqual({ items, next_cursor: "120" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [url, options] = fetcher.mock.calls[0];
    expect(url.origin).toBe("http://public-api:8000");
    expect(url.pathname).toBe(`/v1/${kind}`);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      limit: "60",
      offset: "60",
      [filter]: "true",
      has_articles: "true",
      languages: "en",
    });
    expect(options.headers).toEqual({ Accept: "application/json", "Cache-Control": "max-age=600" });
  },
);
it("bounds offsets, stops on a short batch and cancels the upstream request", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  const response = await topics(
    new Request("https://devfeed.test/api/v1/topics?offset=999999999999", {
      signal: controller.signal,
    }),
  );
  expect(fetcher.mock.calls[0][0].searchParams.get("offset")).toBe("1000000");
  expect(await response.json()).toEqual({ items: [], next_cursor: null });
  controller.abort();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});
it("hides network details on failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private host")));
  const response = await sources(new Request("https://devfeed.test/api/v1/sources"));
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("private host");
});

it.each([
  ["topics", topics],
  ["sources", sources],
] as const)("forwards bounded global search for %s", async (kind, get) => {
  const fetcher = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal("fetch", fetcher);
  await get(new Request(`https://devfeed.test/api/v1/${kind}?q=%20Remote%20%25_%20&offset=60`));
  expect(fetcher.mock.calls[0][0].searchParams.get("q")).toBe("Remote %_");
  expect(fetcher.mock.calls[0][0].searchParams.get("offset")).toBe("60");
});

it.each([
  ["topics", topics],
  ["sources", sources],
] as const)("applies saved languages to %s instead of query overrides", async (kind, get) => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8000");
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ content_types: ["news"], languages: ["fr", "ja"] }))
    .mockResolvedValueOnce(Response.json([]));
  vi.stubGlobal("fetch", fetcher);
  const response = await get(
    new Request(`https://devfeed.test/api/v1/${kind}?language=en&languages=de`, {
      headers: { cookie: "devfeed_user_session=reader" },
    }),
  );
  expect(response.status).toBe(200);
  expect(fetcher.mock.calls[0][0].pathname).toBe("/v1/user/settings/feed");
  expect(fetcher.mock.calls[1][0].searchParams.getAll("languages")).toEqual(["fr", "ja"]);
  expect(fetcher.mock.calls[1][0].searchParams.has("language")).toBe(false);
  expect(fetcher.mock.calls[1][1].headers.Cookie).toBeUndefined();
});

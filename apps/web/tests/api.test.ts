import { afterEach, expect, it, vi } from "vitest";
import {
  getArticle,
  getFeed,
  getSearch,
  getTopic,
  getTopics,
  getSources,
  hasUserSession,
  getReaderFeedPreferences,
  getTrending,
  UserApiError,
} from "@/lib/api";
import { catalogRoute } from "@/lib/server/catalog-route";
import { parseFilters } from "@/lib/feed-query";
import { parseSearchOptions } from "@/lib/search";
const cookie = vi.hoisted(() => ({ value: "" }));
vi.mock("next/headers", () => ({ cookies: async () => ({ toString: () => cookie.value }) }));
afterEach(() => {
  cookie.value = "";
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it.each(["article", "news", "tutorial", "release", "comparison", "opinion"])(
  "forwards the %s search content type to the public API",
  async (type) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ query: "cloud", sections: {} }));
    vi.stubGlobal("fetch", fetcher);
    const options = parseSearchOptions(new URLSearchParams(`content_types=${type}&sort=newest`));
    await getSearch("cloud", options.section, "2", undefined, options);
    const params = fetcher.mock.calls[0][0].searchParams;
    expect(Object.fromEntries(params)).toEqual({
      q: "cloud",
      section: "articles",
      content_types: type,
      sort: "newest",
      page: "2",
    });
  },
);
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
  expect(options.headers).toEqual({
    Accept: "application/json",
    "Cache-Control": "max-age=600",
  });
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
    languages: "en",
  });
});

it("verifies the session and redirects expired cookies returning null", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api.internal:8000");
  cookie.value = "devfeed_user_session=expired; unrelated=private";
  const fetcher = vi.fn().mockResolvedValue(Response.json(null));
  vi.stubGlobal("fetch", fetcher);
  expect(await hasUserSession()).toBe(false);
  expect(fetcher.mock.calls[0][1].headers.Cookie).toBe("devfeed_user_session=expired");
  expect(fetcher.mock.calls[0][1].headers["Cache-Control"]).toBe("no-store");
  fetcher.mockResolvedValue(Response.json({ user_id: "user" }));
  expect(await hasUserSession()).toBe(true);
});

it("omits attribution and unrelated cookies from SSR identity and preferences with the flag enabled", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api.internal:8000");
  vi.stubEnv("DEVFEED_X_PIXEL_ENABLED", "true");
  const reader =
    "devfeed_user_session=session; __Host-devfeed_user_state=state; devfeed_user_visitor=visitor";
  cookie.value = `${reader}; devfeed_user_x_click=click; __Host-devfeed_user_x_click=host-click; _twclid=pixel; devfeed_admin_session=private; unrelated=private`;
  const fetcher = vi
    .fn()
    .mockImplementation((url: URL) =>
      Response.json(
        url.pathname === "/v1/user/auth/me"
          ? { user_id: "user" }
          : { content_types: ["article"], languages: ["en"] },
      ),
    );
  vi.stubGlobal("fetch", fetcher);
  expect(await hasUserSession()).toBe(true);
  await getReaderFeedPreferences();
  await getReaderFeedPreferences(undefined, cookie.value);
  expect(fetcher).toHaveBeenCalledTimes(3);
  for (const [, options] of fetcher.mock.calls) {
    expect(options.headers.Cookie).toBe(reader);
    expect(options.headers["Cache-Control"]).toBe("no-store");
  }
});

it("keeps anonymous trending on the private service with caching disabled", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api.internal:8000");
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api.internal:8001");
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetcher);
  await getTrending("C++ & cursor");
  const [url, options] = fetcher.mock.calls[0];
  expect(url.origin).toBe("http://user-api.internal:8001");
  expect(url.searchParams.get("cursor")).toBe("C++ & cursor");
  expect(options.headers["Cache-Control"]).toBe("no-store");
  expect(options.headers.Cookie).toBeUndefined();
});

it("reports unavailable private service configuration without using the public API", async () => {
  vi.stubEnv("DEVFEED_USER_API_URL", "");
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(getTrending()).rejects.toEqual(new UserApiError(503));
  expect(fetcher).not.toHaveBeenCalled();
});

it("preserves article-count sorting through the topic catalog gateway and pagination", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal("fetch", fetcher);
  const response = await catalogRoute(
    new Request("http://localhost/api/v1/topics?sort=articles&offset=60"),
    "topics",
  );
  expect(response.status).toBe(200);
  const url = fetcher.mock.calls[0][0];
  expect(url.searchParams.get("sort")).toBe("articles");
  expect(url.searchParams.get("offset")).toBe("60");
  expect(url.searchParams.get("has_articles")).toBe("true");
});

import { afterEach, expect, it, vi } from "vitest";
import { GET as topics } from "@/app/api/v1/topics/[slug]/route";
import { GET as tags } from "@/app/api/v1/tags/[slug]/route";

const identity = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const routes = [
  ["topics", topics],
  ["tags", tags],
] as const;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each(routes)(
  "resolves a saved %s UUID through one cancellable public read without credentials",
  async (kind, get) => {
    vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
    const selection = { id: identity, name: "Renamed selection", slug: "current-name" };
    const fetcher = vi.fn().mockResolvedValue(Response.json(selection));
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    const response = await get(
      new Request(
        `https://devfeed.test/api/v1/${kind}/${identity}?by_id=true&url=https://evil.example/&private=true`,
        {
          headers: { cookie: "devfeed_user_session=private", authorization: "Bearer private" },
          signal: controller.signal,
        },
      ),
      { params: Promise.resolve({ slug: identity }) },
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(selection);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, options] = fetcher.mock.calls[0];
    expect(url.origin).toBe("http://public-api:8000");
    expect(url.pathname).toBe(`/v1/${kind}/${identity}`);
    expect(Object.fromEntries(url.searchParams)).toEqual({ by_id: "true" });
    expect(options.headers).toEqual({ Accept: "application/json", "Cache-Control": "max-age=600" });
    expect(options.cache).toBe("no-store");
    expect(options.redirect).toBe("error");
    controller.abort();
    expect(options.signal.aborted).toBe(true);
  },
);

it.each(routes)(
  "keeps a UUID-shaped %s slug distinct from an explicit saved-identity lookup",
  async (kind, get) => {
    const bySlug = {
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      name: "UUID slug",
      slug: identity,
    };
    const byIdentity = { id: identity, name: "Saved selection", slug: "saved-selection" };
    const fetcher = vi.fn(async (url: URL) =>
      Response.json(url.searchParams.get("by_id") === "true" ? byIdentity : bySlug),
    );
    vi.stubGlobal("fetch", fetcher);
    for (const [query, expected] of [
      ["?by_id=true", byIdentity],
      ["", bySlug],
      ["?by_id=false", bySlug],
    ] as const) {
      const response = await get(
        new Request(`https://devfeed.test/api/v1/${kind}/${identity}${query}`),
        { params: Promise.resolve({ slug: identity }) },
      );
      expect(await response.json()).toEqual(expected);
    }
    expect(fetcher).toHaveBeenCalledTimes(3);
  },
);

it.each(routes)(
  "distinguishes unavailable %s selections from retryable service failures",
  async (kind, get) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    for (const status of [404, 503]) {
      fetcher.mockResolvedValueOnce(
        Response.json({ detail: "private upstream details" }, { status }),
      );
      const response = await get(
        new Request(`https://devfeed.test/api/v1/${kind}/${identity}?by_id=true`),
        { params: Promise.resolve({ slug: identity }) },
      );
      expect(response.status).toBe(status);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual({
        detail: `Couldn’t load ${kind === "topics" ? "topic" : "tag"}`,
      });
    }
    fetcher.mockRejectedValueOnce(new Error("private upstream hostname"));
    const response = await get(
      new Request(`https://devfeed.test/api/v1/${kind}/${identity}?by_id=true`),
      { params: Promise.resolve({ slug: identity }) },
    );
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("private upstream");
  },
);

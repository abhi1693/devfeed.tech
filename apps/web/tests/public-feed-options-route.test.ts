import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/feed/options/route";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("returns contextual options through a fixed public endpoint without credentials", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const options = { sources: [], languages: ["en"], content_types: ["news"] };
  const fetcher = vi.fn().mockResolvedValue(Response.json(options));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request(
      "https://devfeed.test/api/v1/feed/options?content_type=news&language=en&q=Rust&cursor=private&url=https://other.test",
      { headers: { cookie: "secret=session", authorization: "Bearer secret" } },
    ),
  );
  expect(await response.json()).toEqual(options);
  expect(response.headers.get("cache-control")).toBe("no-store");
  const [url, init] = fetcher.mock.calls[0];
  expect(url.origin + url.pathname).toBe("http://public-api:8000/v1/feed/options");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    content_type: "news",
    languages: "en",
    q: "Rust",
  });
  expect(init.headers).toEqual({ Accept: "application/json", "Cache-Control": "max-age=600" });
});

it("forwards cancellation and hides upstream failure details", async () => {
  const controller = new AbortController();
  const fetcher = vi.fn().mockRejectedValue(new Error("private backend address"));
  vi.stubGlobal("fetch", fetcher);
  const response = await GET(
    new Request("https://devfeed.test/api/v1/feed/options", { signal: controller.signal }),
  );
  controller.abort();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("private backend");
});

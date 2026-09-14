import { afterEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/articles/[slug]/route";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
const request = (slug: string) =>
  GET(
    new Request(`https://devfeed.test/api/v1/articles/${slug}`, {
      headers: { cookie: "secret=session", authorization: "Bearer secret" },
    }),
    { params: Promise.resolve({ slug }) },
  );
it("rejects invalid slugs without contacting the backend", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  expect((await request("../private")).status).toBe(404);
  expect(fetcher).not.toHaveBeenCalled();
});
it("loads public article and primary topic without forwarding credentials", async () => {
  vi.stubEnv("DEVFEED_PUBLIC_API_URL", "http://public-api:8000");
  const article = { slug: "test-article", topics: [{ slug: "python", role: "primary" }] };
  const topic = { slug: "python", description: "Full description" };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json(article))
    .mockResolvedValueOnce(Response.json(topic));
  vi.stubGlobal("fetch", fetcher);
  const response = await request("test-article");
  expect(await response.json()).toEqual({ article, topic });
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(String(fetcher.mock.calls[0][0])).toBe("http://public-api:8000/v1/articles/test-article");
  for (const [, init] of fetcher.mock.calls)
    expect(init.headers).toEqual({ Accept: "application/json" });
});
it("preserves the article when the optional topic fails and sanitizes article errors", async () => {
  const article = { topics: [{ slug: "python" }] };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json(article))
      .mockRejectedValue(new Error("private backend")),
  );
  expect(await (await request("article")).json()).toEqual({ article, topic: null });
  const response = await request("article");
  expect(response.status).toBe(503);
  expect(await response.text()).not.toContain("private backend");
});

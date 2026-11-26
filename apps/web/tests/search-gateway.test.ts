import { beforeEach, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/search/route";
import { getSearch } from "@/lib/api";
import { contentTypes } from "@/lib/feed-query";

// Unit requests have no Next response lifecycle; reader-gateway.mjs covers HTTP completion.
vi.mock("next/server", () => ({ after: (callback: () => void) => callback() }));

vi.mock("@/lib/api", () => ({ getSearch: vi.fn(), UserApiError: class extends Error {} }));
vi.mock("@devfeed/telemetry/propagation", () => ({
  traceHeaders: () => ({ traceparent: "trace" }),
}));

beforeEach(() => vi.clearAllMocks());
it.each(contentTypes)("preserves %s content filtering through the search gateway", async (type) => {
  vi.mocked(getSearch).mockResolvedValue({ query: "cloud", sections: {} });
  const response = await GET(
    new Request(
      `https://devfeed.tech/api/v1/search?q=cloud&section=articles&content_types=${type}&page=2`,
    ),
  );
  expect(response.status).toBe(200);
  expect(getSearch).toHaveBeenLastCalledWith(
    "cloud",
    "articles",
    "2",
    expect.any(AbortSignal),
    expect.objectContaining({ content_type: type }),
  );
});

it.each([
  "content_types=private",
  "content_types=news&content_types=release",
  "section=topics&content_types=news",
])("rejects invalid or conflicting content filters: %s", async (filters) => {
  vi.mocked(getSearch).mockClear();
  const response = await GET(new Request(`https://devfeed.tech/api/v1/search?q=cloud&${filters}`));
  expect(response.status).toBe(422);
  expect(getSearch).not.toHaveBeenCalled();
});

it("rejects NUL search requests before upstream work and keeps punctuation literal", async () => {
  const invalid = await GET(
    new Request("https://devfeed.tech/api/v1/search?" + new URLSearchParams({ q: "python\0" })),
  );
  expect(invalid.status).toBe(422);
  expect(invalid.headers.get("cache-control")).toBe("no-store");
  expect(getSearch).not.toHaveBeenCalled();
  const query = 'C++ <img alt="example"> SELECT';
  vi.mocked(getSearch).mockResolvedValue({ query, sections: {} });
  expect(
    (
      await GET(
        new Request("https://devfeed.tech/api/v1/search?" + new URLSearchParams({ q: query })),
      )
    ).status,
  ).toBe(200);
  expect(getSearch).toHaveBeenCalledWith(
    query,
    undefined,
    "1",
    expect.any(AbortSignal),
    expect.any(Object),
  );
});

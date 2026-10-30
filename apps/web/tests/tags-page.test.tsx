import { beforeEach, expect, it, vi } from "vitest";
import Tags, { generateMetadata } from "@/app/tags/page";
import { getTags, UserApiError } from "@/lib/api";
import type { SearchParams } from "@/lib/feed-query";

vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getTags: vi.fn(),
}));
vi.mock("@/components/user-shell", () => ({ UserShell: () => null }));
beforeEach(() => vi.mocked(getTags).mockReset().mockResolvedValue([]));

it.each<[SearchParams, number, string, string]>([
  [{}, 0, "", ""],
  [{ q: "  C++  ", letter: "C", offset: "60" }, 60, "C++", "C"],
  [{ q: ["ignored"], letter: "cc", offset: "-1" }, 0, "", ""],
  [{ q: "x".repeat(201), letter: "#", offset: "1000001" }, 1000000, "x".repeat(200), "#"],
])(
  "normalizes directory filters before loading a bounded page: %j",
  async (params, offset, query, letter) => {
    const tags = [{ id: "cpp", name: "C++", slug: "c++" }];
    vi.mocked(getTags).mockResolvedValue(tags);
    const page = await Tags({ searchParams: Promise.resolve(params) });
    expect(getTags).toHaveBeenCalledWith(offset, 61, query, letter);
    expect(page.props.section).toBe("tags");
    expect(page.props.children.props).toEqual({ tags, query, letter, nextCursor: null, offset });
    expect(page.props.children.key).toBe(`${query}:${letter}:${offset}`);
  },
);

it("continues a full directory page using its bounded catalog cursor", async () => {
  vi.mocked(getTags).mockResolvedValue(
    Array.from({ length: 61 }, (_, id) => ({
      id: String(id),
      name: `Tag ${id}`,
      slug: `tag-${id}`,
    })),
  );
  const page = await Tags({ searchParams: Promise.resolve({ offset: "60" }) });
  expect(page.props.children.props.nextCursor).toBe("120");
});

it("stops tag pagination when a direct continuation page has exactly 60 results", async () => {
  vi.mocked(getTags).mockResolvedValue(
    Array.from({ length: 60 }, (_, id) => ({
      id: String(id),
      name: `Tag ${id}`,
      slug: `tag-${id}`,
    })),
  );
  const page = await Tags({
    searchParams: Promise.resolve({ offset: "60", q: "C++", letter: "C" }),
  });
  expect(getTags).toHaveBeenCalledWith(60, 61, "C++", "C");
  expect(page.props.children.props.nextCursor).toBeNull();
  expect(page.props.children.props.tags).toHaveLength(60);
});

it("canonicalizes tag discovery independently of transient search filters", async () => {
  vi.mocked(getTags).mockResolvedValue([{ id: "cpp", slug: "c++", name: "C++" }]);
  const metadata = await generateMetadata({
    searchParams: Promise.resolve({ q: "C++", letter: "C", offset: "60" }),
  });
  expect(metadata.title).toBe("Explore tags");
  expect(metadata.description).toBe("Discover developer articles by tag.");
  expect(metadata.alternates?.canonical).toBe("https://devfeed.tech/tags?offset=60");
});

it("preserves upstream outages so the route can show its error boundary", async () => {
  vi.mocked(getTags).mockRejectedValueOnce(new Error("tag service unavailable"));
  await expect(Tags({ searchParams: Promise.resolve({}) })).rejects.toThrow(
    "tag service unavailable",
  );
});

it.each([502, 503, 504])(
  "renders directory recovery and noindex metadata for %s",
  async (status) => {
    vi.mocked(getTags).mockRejectedValue(new UserApiError(status));
    const props = { searchParams: Promise.resolve({ q: "Rust", letter: "R" }) };
    expect(await generateMetadata(props)).toMatchObject({ robots: { index: false } });
    expect(await Tags(props)).toMatchObject({ props: { kind: "tags" } });
    vi.mocked(getTags).mockResolvedValue([{ id: "rust", slug: "rust", name: "Rust" }]);
    expect(await Tags(props)).toMatchObject({ props: { section: "tags" } });
    expect(await generateMetadata(props)).toMatchObject({ robots: { index: false, follow: true } });
  },
);

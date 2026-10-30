import { beforeEach, expect, it, vi } from "vitest";
import Topics, { generateMetadata as topicsMetadata } from "@/app/topics/page";
import Sources, { generateMetadata as sourcesMetadata } from "@/app/sources/page";
import Tags, { generateMetadata as tagsMetadata } from "@/app/tags/page";
import { catalogFilters, catalogHref } from "@/lib/catalog-page";
import { getSources, getTags, getTopics, UserApiError } from "@/lib/api";
import type { SearchParams } from "@/lib/feed-query";
import { source, topic } from "./fixtures";

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getTopics: vi.fn(),
  getSources: vi.fn(),
  getTags: vi.fn(),
}));
const tag = { id: "tag", slug: "tag", name: "Tag" };
const directories = [
  { kind: "topics", Page: Topics, metadata: topicsMetadata, load: getTopics, item: topic },
  { kind: "sources", Page: Sources, metadata: sourcesMetadata, load: getSources, item: source },
  { kind: "tags", Page: Tags, metadata: tagsMetadata, load: getTags, item: tag },
] as const;
beforeEach(() => {
  vi.mocked(getTopics).mockReset().mockResolvedValue([topic]);
  vi.mocked(getSources).mockReset().mockResolvedValue([source]);
  vi.mocked(getTags).mockReset().mockResolvedValue([tag]);
});

it.each(directories)(
  "$kind metadata and page reject nonexistent continuations",
  async ({ Page, metadata, load }) => {
    vi.mocked(load).mockResolvedValue([]);
    for (const offset of ["60", "999999", "9".repeat(400)]) {
      const props = { searchParams: Promise.resolve({ offset }) };
      await expect(metadata(props)).rejects.toThrow("NOT_FOUND");
      await expect(Page(props)).rejects.toThrow("NOT_FOUND");
    }
  },
);

it.each(directories)(
  "$kind keeps first-page empty searches useful and noindex",
  async ({ kind, Page, metadata, load }) => {
    vi.mocked(load).mockResolvedValue([]);
    for (const query of [{}, { q: "unmatched" }, ...(kind === "tags" ? [{ letter: "Z" }] : [])]) {
      const props = { searchParams: Promise.resolve(query) };
      expect(await metadata(props)).toMatchObject({
        alternates: { canonical: `https://devfeed.tech/${kind}` },
        robots: { index: false, follow: true },
      });
      await expect(Page(props)).resolves.toBeTruthy();
    }
    await expect(
      Page({ searchParams: Promise.resolve({ q: "unmatched", offset: "60" }) }),
    ).rejects.toThrow("NOT_FOUND");
  },
);

it.each(directories)(
  "$kind keeps valid page canonicals and indexing consistent",
  async ({ kind, metadata }) => {
    expect(
      await metadata({
        searchParams: Promise.resolve({ offset: "00060", utm_source: "tracking" }),
      }),
    ).toMatchObject({
      alternates: { canonical: `https://devfeed.tech/${kind}?offset=60` },
    });
    expect((await metadata({ searchParams: Promise.resolve({}) })).robots).toBeUndefined();
    expect(
      await metadata({ searchParams: Promise.resolve({ q: "  Directory  ", offset: "60" }) }),
    ).toMatchObject({
      alternates: { canonical: `https://devfeed.tech/${kind}?offset=60` },
      robots: { index: false, follow: true },
    });
  },
);

it.each(directories)(
  "$kind distinguishes an exact final page from a real lookahead",
  async ({ kind, Page, load, item }) => {
    for (const count of [60, 61]) {
      vi.mocked(load).mockResolvedValue(
        Array.from({ length: count }, (_, i) => ({ ...item, id: String(i) })) as never,
      );
      const result = await Page({ searchParams: Promise.resolve({ offset: "60" }) });
      const content = kind === "tags" ? result.props.children : await result.props.content;
      expect(content.props[kind]).toHaveLength(60);
      expect(
        kind === "tags" ? content.props.nextCursor : content.props.initialPage.next_cursor,
      ).toBe(count === 61 ? "120" : null);
    }
  },
);

it.each(directories)(
  "$kind outages retain retry semantics rather than becoming 404",
  async ({ Page, metadata, load }) => {
    vi.mocked(load).mockRejectedValue(new UserApiError(503));
    const props = { searchParams: Promise.resolve({ offset: "60" }) };
    expect(await metadata(props)).toMatchObject({ robots: { index: false, follow: false } });
    expect(await Page(props)).toMatchObject({ props: { kind: expect.any(String) } });
    vi.mocked(load).mockRejectedValue(new Error("Unexpected upstream failure"));
    await expect(Page(props)).rejects.toThrow("Unexpected upstream failure");
  },
);

it.each(["topics", "sources", "tags"] as const)(
  "normalizes %s URL and object parameters identically",
  (kind) => {
    for (const value of [
      "-1",
      "not-a-number",
      "1.5",
      "1e3",
      " 60 ",
      "+60",
      "",
      "00060",
      "9".repeat(400),
    ]) {
      const params = { offset: value, q: "  C++  ", letter: "C" };
      expect(catalogFilters(new URLSearchParams(params), kind)).toEqual(
        catalogFilters(params, kind),
      );
    }
    const repeated: SearchParams = { offset: ["60", "120"], q: ["one", "two"], letter: ["A", "B"] };
    expect(
      catalogFilters(
        new URLSearchParams("offset=60&offset=120&q=one&q=two&letter=A&letter=B"),
        kind,
      ),
    ).toEqual(catalogFilters(repeated, kind));
    expect(catalogFilters(repeated, kind)).toEqual({ offset: 0, query: "", letter: "" });
    expect(catalogFilters({ q: "x".repeat(300), letter: "AA" }, kind)).toEqual({
      offset: 0,
      query: "x".repeat(200),
      letter: "",
    });
    expect(catalogHref(kind, { query: "C++", letter: "C", offset: 60 })).toBe(
      `/${kind}?q=C%2B%2B${kind === "tags" ? "&letter=C" : ""}&offset=60`,
    );
  },
);

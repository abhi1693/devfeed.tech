// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchResults } from "@/components/search-results";
import { readerRequest } from "@/lib/reader-runtime";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import type { SearchHit, SearchResponse } from "@/lib/search";
import type { ReactNode } from "react";
import { article } from "./fixtures";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false, sessionRevision: 0 }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
vi.mock("@/components/source-follow", () => ({ SourceFollow: () => null }));
vi.mock("@/lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("@/lib/reader-runtime", async (original) => ({
  ...(await original<typeof import("@/lib/reader-runtime")>()),
  readerRequest: vi.fn(),
}));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => vi.fn()) }));
vi.mock("@/components/article-engagement", () => ({
  EngagementProvider: ({ children }: { children: ReactNode }) => children,
  ArticleEngagement: () => null,
  ArticleBookmarkButton: () => null,
  ArticleReadLink: ({
    children,
    articleId,
    ...props
  }: {
    children: ReactNode;
    articleId: string;
  }) => {
    void articleId;
    return <a {...props}>{children}</a>;
  },
  useArticleEngagement: () => null,
}));

const hits: SearchHit[] = Array.from({ length: 24 }, (_, index) => ({
  id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
  title: `Search article ${index}`,
  description: "A matching article about TypeScript validation.",
  href: `/articles/search-article-${index}`,
  image_url: null,
  label: "tutorial",
  published_at: null,
}));
const result = (items = hits, next_cursor: string | null = null): SearchResponse => ({
  query: "typescript",
  sections: { articles: { items, next_cursor } },
});
const engagement = { ...sponsoredPlacement, ad_format: "engagement" as const };
let intersect: () => void;
beforeEach(() => {
  localStorage.clear();
  vi.mocked(readerRequest)
    .mockReset()
    .mockImplementation(async () =>
      Response.json({ items: [{ ...placement, ad_format: "engagement" }, engagement] }),
    );
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        intersect = () =>
          callback(
            [{ isIntersecting: true } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          );
      }
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("places one compact contextual product above Topics without changing organic search results", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  const data = result();
  data.sections.topics = {
    items: [{ ...hits[0], title: "TypeScript", href: "/topics/typescript" }],
    next_cursor: null,
  };
  const view = render(<SearchResults result={data} />);
  const list = view.container.querySelector(".search-section-articles .search-result-list")!;
  const rows = Array.from(list.children);
  const reading = screen.getByRole("link", { name: hits[8].title });
  reading.focus();
  await act(async () =>
    finish(Response.json({ items: [{ ...placement, ad_format: "engagement" }, engagement] })),
  );
  const card = screen.getByRole("link", { name: engagement.name }).closest("article")!;
  const sidebar = view.container.querySelector("aside.search-sidebar")!;
  const related = sidebar.querySelector(".search-related-results")!;
  expect(card.closest("aside")).toBe(sidebar);
  expect(card.compareDocumentPosition(related) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(sidebar.querySelectorAll(".partner-placement")).toHaveLength(1);
  expect(card.getAttribute("data-ad-format")).toBe("engagement");
  expect(card.className).toContain("partner-placement--compact");
  expect(card.querySelector(".card-image")).toBeNull();
  expect(card.textContent).toContain(engagement.tagline);
  expect(card.textContent).toContain("Sponsored");
  expect(card.textContent).not.toContain("Explore");
  expect(card.querySelector(".partner-placement-destination")?.textContent).toBe("partner.example");
  expect(card.querySelector(".partner-placement-destination svg")).toBeNull();
  expect(rows).toHaveLength(24);
  expect(Array.from(list.children)).toEqual(rows);
  expect(list.querySelector(".partner-placement")).toBeNull();
  expect(screen.getByRole("link", { name: hits[8].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(
    Array.from(list.querySelectorAll(".search-result-link")).map((link) => link.textContent),
  ).toEqual(hits.map((hit) => hit.title));
  expect(view.container.querySelector(".search-section-articles .partner-placements")).toBeNull();
  expect(sidebar.querySelectorAll("section.partner-placements.search-engagement")).toHaveLength(1);
  expect(screen.queryByText(placement.reason)).toBeNull();
  expect(screen.queryByText("Relevant product")).toBeNull();
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    surface: "search",
    allow_sponsored_fallback: false,
    ad_formats: ["engagement"],
    article_ids: hits.map((hit) => hit.id),
    exclude_product_ids: [],
  });
});

it("keeps an article-only sidebar contextual while respecting search filters", async () => {
  const view = render(
    <SearchResults
      result={result()}
      options={{
        section: "articles",
        content_type: "tutorial",
        sort: "newest",
        date_from: "2026-09-01",
        date_to: "2026-10-01",
      }}
    />,
  );
  await screen.findByRole("link", { name: engagement.name });
  expect(view.container.querySelector("aside.search-sidebar .partner-placement")).toBeTruthy();
  expect(view.container.querySelector(".search-related-results")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Topics" })).toBeNull();
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(24);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject({
    allow_sponsored_fallback: false,
    ad_formats: ["engagement"],
    article_ids: hits.map((hit) => hit.id),
  });
});

it("preserves a focused Topic link and its reading position when the sidebar product arrives", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () => new Promise((resolve) => (finish = resolve)),
  );
  const data = result();
  data.sections.topics = {
    items: [{ ...hits[0], title: "TypeScript", href: "/topics/typescript" }],
    next_cursor: null,
  };
  const view = render(<SearchResults result={data} />);
  const topic = screen.getByRole("link", { name: "TypeScript" });
  const row = topic.closest<HTMLElement>(".search-result")!;
  const scroller = document.scrollingElement ?? document.documentElement;
  const viewport = vi
    .spyOn(scroller, "getBoundingClientRect")
    .mockReturnValue(new DOMRect(0, 0, 1280, 900));
  const bounds = vi.spyOn(row, "getBoundingClientRect").mockImplementation(() => {
    const addedHeight = view.container.querySelector(".search-engagement") ? 180 : 0;
    return new DOMRect(0, 100 + addedHeight - scroller.scrollTop, 250, 60);
  });
  try {
    topic.focus();
    const top = row.getBoundingClientRect().top;
    await act(async () => finish(Response.json({ items: [engagement] })));
    expect(screen.getByRole("link", { name: "TypeScript" })).toBe(topic);
    expect(document.activeElement).toBe(topic);
    expect(topic.closest(".search-result")).toBe(row);
    expect(scroller.scrollTop).toBe(180);
    expect(row.getBoundingClientRect().top).toBe(top);
    expect(view.container.querySelectorAll(".search-section-articles .search-result")).toHaveLength(
      24,
    );
  } finally {
    bounds.mockRestore();
    viewport.mockRestore();
    scroller.scrollTop = 0;
  }
});

it("does not display Feed or sponsored-content cards returned to an Engagement-only search", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({
      items: [
        placement,
        {
          ...sponsoredPlacement,
          ad_format: "sponsored_content",
          article,
          url: `/articles/${article.slug}`,
        },
      ],
    }),
  );
  const view = render(<SearchResults result={result()} />);
  await act(async () => {});
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(24);
});

it.each(["topics", "sources", "tags"] as const)(
  "does not insert article products into %s search results",
  async (kind) => {
    const view = render(
      <SearchResults
        result={{
          query: "typescript",
          sections: {
            articles: { items: hits, next_cursor: null },
            [kind]: { items: hits.slice(0, 1), next_cursor: null },
          },
        }}
        options={{ section: kind, sort: "relevance", date_from: "", date_to: "" }}
      />,
    );
    await act(async () => {});
    expect(view.container.querySelector(".partner-placement")).toBeNull();
    expect(readerRequest).not.toHaveBeenCalled();
  },
);

it.each([0, 1, 2, 3])(
  "keeps sparse %s-article search results organic without requesting products",
  async (count) => {
    const items = hits.slice(0, count);
    const view = render(<SearchResults result={result(items)} />);
    await act(async () => {});
    expect(readerRequest).not.toHaveBeenCalled();
    expect(view.container.querySelector(".partner-placement")).toBeNull();
    expect(
      Array.from(
        view.container.querySelectorAll(".search-result-link"),
        (link) => link.textContent,
      ),
    ).toEqual(items.map((hit) => hit.title));
  },
);

it("keeps first-page search context and product density when more results append", async () => {
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements") ? { items: [engagement] } : result(hits.slice(4)),
    ),
  );
  const view = render(<SearchResults result={result(hits.slice(0, 4), "2")} />);
  const card = (await screen.findByRole("link", { name: engagement.name })).closest("article");
  const firstRows = Array.from(view.container.querySelectorAll(".search-result"));
  await act(async () => intersect());
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(24);
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(1);
  expect(view.container.querySelector(".partner-placement")).toBe(card);
  expect(Array.from(view.container.querySelectorAll(".search-result")).slice(0, 4)).toEqual(
    firstRows,
  );
  expect(view.container.querySelector(".search-result-list .partner-placement")).toBeNull();
  const deliveries = vi
    .mocked(readerRequest)
    .mock.calls.filter(([path]) => String(path).includes("partner-placements"));
  expect(deliveries).toHaveLength(1);
  expect(JSON.parse(String(deliveries[0][1]?.body)).article_ids).toEqual(
    hits.slice(0, 4).map((hit) => hit.id),
  );
});

it("expires a sidebar receipt without replacing organic rows, moving focus or requesting new ads", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T12:00:00Z"));
  const hit = {
    ...hits[0],
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    title: "Another matching article",
    href: "/articles/another-matching-article",
  };
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements")
        ? {
            items: [
              {
                ...engagement,
                receipt: partnerReceipt({ assetId: engagement.asset_id }),
              },
            ],
          }
        : result([hit]),
    ),
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SearchResults result={result(hits, "2")} />);
  });
  const card = screen.getByRole("link", { name: engagement.name }).closest("article")!;
  const stopTracking = vi.mocked(observePartnerPlacement).mock.results.at(-1)!.value;
  await act(async () => intersect());
  const rows = Array.from(view.container.querySelectorAll(".search-result"));
  const reading = screen.getByRole("link", { name: hits[8].title });
  reading.focus();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 + 1000);
  });

  expect(view.container.querySelector(".partner-placement")).toBe(card);
  expect(card.getAttribute("data-suspended")).toBe("true");
  expect(card.hasAttribute("inert")).toBe(true);
  expect(card.hidden).toBe(false);
  expect(stopTracking).toHaveBeenCalled();
  expect(screen.queryByRole("link", { name: engagement.name })).toBeNull();
  expect(Array.from(view.container.querySelectorAll(".search-result"))).toEqual(rows);
  expect(
    Array.from(view.container.querySelectorAll(".search-result-link"), (link) => link.textContent),
  ).toEqual([...hits, hit].map((item) => item.title));
  expect(screen.getByRole("link", { name: hits[8].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(
    vi
      .mocked(readerRequest)
      .mock.calls.filter(([path]) => String(path).includes("partner-placements")),
  ).toHaveLength(1);
});

it("cancels a previous search delivery when a new query has no matching articles", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<SearchResults result={result()} />);
  const signal = vi.mocked(readerRequest).mock.calls[0][1]!.signal as AbortSignal;
  view.rerender(
    <SearchResults
      result={{ query: "no-match", sections: { articles: { items: [], next_cursor: null } } }}
    />,
  );
  expect(signal.aborted).toBe(true);
  await act(async () => finish(Response.json({ items: [engagement] })));
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(screen.getByRole("heading", { name: "No results for “no-match”" })).toBeTruthy();
});

it("replaces a pending sidebar context when a different search has article results", async () => {
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest)
    .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    .mockResolvedValueOnce(Response.json({ items: [engagement] }));
  const view = render(<SearchResults result={result()} />);
  const previous = vi.mocked(readerRequest).mock.calls[0][1]!.signal as AbortSignal;
  const nextHits = hits.slice(0, 4).map((hit, index) => ({
    ...hit,
    id: `bbbbbbbb-bbbb-4bbb-8bbb-${String(index).padStart(12, "0")}`,
    title: `Rust result ${index}`,
  }));
  view.rerender(<SearchResults result={{ ...result(nextHits), query: "rust" }} />);
  await screen.findByRole("link", { name: engagement.name });
  expect(previous.aborted).toBe(true);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[1][1]?.body)).article_ids).toEqual(
    nextHits.map((hit) => hit.id),
  );
  await act(async () =>
    finish(
      Response.json({ items: [{ ...placement, name: "Stale product", ad_format: "engagement" }] }),
    ),
  );
  expect(screen.queryByRole("link", { name: "Stale product" })).toBeNull();
  expect(screen.getByRole("link", { name: engagement.name })).toBeTruthy();
  expect(
    Array.from(view.container.querySelectorAll(".search-result-link"), (link) => link.textContent),
  ).toEqual(nextHits.map((hit) => hit.title));
});

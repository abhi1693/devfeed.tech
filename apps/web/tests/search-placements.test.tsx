// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchResults } from "@/components/search-results";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import type { SearchHit, SearchResponse } from "@/lib/search";
import type { ReactNode } from "react";
import { article } from "./fixtures";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

const session = vi.hoisted(() => ({
  user: null as null | { user_id: string; csrf_token: string },
  loading: false,
  sessionRevision: 0,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
vi.mock("@/components/source-follow", () => ({ SourceFollow: () => null }));
vi.mock("@/lib/analytics", () => ({ trackEvent: vi.fn() }));
vi.mock("@/lib/reader-runtime", async (original) => ({
  ...(await original<typeof import("@/lib/reader-runtime")>()),
  readerRequest: vi.fn(),
}));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => vi.fn()) }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
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

const makeHits = (count: number, start = 0): SearchHit[] =>
  Array.from({ length: count }, (_, offset) => {
    const index = start + offset;
    return {
      id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
      title: `Search article ${index}`,
      description: "A matching article about TypeScript validation.",
      href: `/articles/search-article-${index}`,
      image_url: null,
      label: "tutorial",
      published_at: null,
    };
  });
const hits = makeHits(24);
const result = (items = hits, next_cursor: string | null = null): SearchResponse => ({
  query: "typescript",
  sections: { articles: { items, next_cursor } },
});
const engagement = { ...sponsoredPlacement, ad_format: "engagement" as const };
const deliveries = () =>
  vi
    .mocked(readerRequest)
    .mock.calls.filter(([path]) => String(path).includes("partner-placements"));
const deliveryContext = (index = 0) => JSON.parse(String(deliveries()[index][1]?.body));
let intersect: () => void;
beforeEach(() => {
  session.user = null;
  session.loading = false;
  session.sessionRevision = 0;
  localStorage.clear();
  vi.mocked(userRequest)
    .mockReset()
    .mockResolvedValue({ items: [engagement] });
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
    search: {
      query: "typescript",
      results: { articles: hits.map((hit) => hit.id), topics: [hits[0].id] },
    },
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
    search: { query: "typescript", results: { articles: hits.map((hit) => hit.id) } },
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
  "offers one sidebar Engagement placement for a single matching %s result",
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
    await screen.findByRole("link", { name: engagement.name });
    const card = view.container.querySelector(".partner-placement")!;
    expect(card.closest("aside.search-sidebar")).toBeTruthy();
    expect(card.getAttribute("data-ad-format")).toBe("engagement");
    expect(view.container.querySelector(".search-result-list .partner-placement")).toBeNull();
    expect(view.container.querySelectorAll(".search-result")).toHaveLength(1);
    expect(deliveryContext()).toEqual({
      surface: "search",
      search: { query: "typescript", results: { [kind]: [hits[0].id] } },
      allow_sponsored_fallback: false,
      ad_formats: ["engagement"],
      exclude_product_ids: [],
    });
  },
);

it.each([1, 2, 3])(
  "lets relevance decide the sidebar placement for %s matching articles",
  async (count) => {
    const items = hits.slice(0, count);
    const view = render(<SearchResults result={result(items)} />);
    await screen.findByRole("link", { name: engagement.name });
    expect(view.container.querySelectorAll("aside.search-sidebar .partner-placement")).toHaveLength(
      1,
    );
    expect(deliveryContext()).toMatchObject({
      surface: "search",
      search: { query: "typescript", results: { articles: items.map((hit) => hit.id) } },
      allow_sponsored_fallback: false,
      ad_formats: ["engagement"],
    });
    expect(
      Array.from(
        view.container.querySelectorAll(".search-result-link"),
        (link) => link.textContent,
      ),
    ).toEqual(items.map((hit) => hit.title));
  },
);

it("does not request products when there are no matching results", async () => {
  const view = render(<SearchResults result={result([])} />);
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(screen.getByRole("heading", { name: "No results for “typescript”" })).toBeTruthy();
});

it("preserves the first loaded results when an initially empty page gains sidebar eligibility", async () => {
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements")
        ? { items: [engagement] }
        : result(hits.slice(0, 1)),
    ),
  );
  const view = render(<SearchResults result={result([], "2")} />);
  expect(deliveries()).toHaveLength(0);
  await act(async () => intersect());
  await screen.findByRole("link", { name: engagement.name });
  expect(screen.getByRole("link", { name: hits[0].title })).toBeTruthy();
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(1);
  expect(deliveries()).toHaveLength(1);
  expect(deliveryContext().search).toEqual({
    query: "typescript",
    results: { articles: [hits[0].id] },
  });
});

it("keeps nonempty results organic when the server finds no relevant product", async () => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [] }));
  const view = render(<SearchResults result={result(hits.slice(0, 1))} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  await act(async () => {});
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(screen.getByRole("link", { name: hits[0].title })).toBeTruthy();
  expect(deliveryContext().allow_sponsored_fallback).toBe(false);
});

it("includes matching context beyond the first 24 initial articles", async () => {
  const items = makeHits(30);
  const view = render(<SearchResults result={result(items)} />);
  await screen.findByRole("link", { name: engagement.name });
  expect(deliveryContext().search.results.articles).toEqual(items.map((hit) => hit.id));
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(30);
});

it("keeps a delivered sidebar product stable when more results append", async () => {
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
  expect(deliveries()).toHaveLength(1);
  expect(deliveryContext().search.results.articles).toEqual(hits.slice(0, 4).map((hit) => hit.id));
});

it("reassesses empty delivery using articles loaded after the initial 24", async () => {
  const later = makeHits(12, 24);
  let attempts = 0;
  vi.mocked(readerRequest).mockImplementation(async (path) => {
    if (String(path).includes("partner-placements"))
      return Response.json({ items: ++attempts === 1 ? [] : [engagement] });
    return Response.json(result([hits[23], ...later]));
  });
  const view = render(<SearchResults result={result(hits, "2")} />);
  await waitFor(() => expect(deliveries()).toHaveLength(1));
  const firstRows = Array.from(view.container.querySelectorAll(".search-result"));
  const reading = screen.getByRole("link", { name: hits[8].title });
  reading.focus();
  await act(async () => intersect());
  await screen.findByRole("link", { name: engagement.name });
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(36);
  expect(Array.from(view.container.querySelectorAll(".search-result")).slice(0, 24)).toEqual(
    firstRows,
  );
  expect(document.activeElement).toBe(reading);
  expect(deliveries()).toHaveLength(2);
  expect(deliveryContext(1).search).toEqual({
    query: "typescript",
    results: { articles: [...hits, ...later].map((hit) => hit.id) },
  });
  expect(deliveryContext(1).allow_sponsored_fallback).toBe(false);
});

it("rolls a full context forward so later results can qualify without an unbounded request", async () => {
  const initial = makeHits(120);
  const later = makeHits(24, 120);
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(String(path).includes("partner-placements") ? { items: [] } : result(later)),
  );
  const view = render(<SearchResults result={result(initial, "2")} />);
  await waitFor(() => expect(deliveries()).toHaveLength(1));
  expect(deliveryContext().search.results.articles).toEqual(initial.map((hit) => hit.id));
  await act(async () => intersect());
  await waitFor(() => expect(deliveries()).toHaveLength(2));
  expect(deliveryContext(1).search.results.articles).toEqual(
    [...initial.slice(24), ...later].map((hit) => hit.id),
  );
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(144);
  expect(view.container.querySelector(".partner-placement")).toBeNull();
});

it("reconsiders the final loaded page when sign-in leaves the previous ad context empty", async () => {
  const later = makeHits(12, 24);
  const initial = result(hits, "2");
  let finish!: (response: unknown) => void;
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements") ? { items: [engagement] } : result(later),
    ),
  );
  vi.mocked(userRequest)
    .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    .mockResolvedValueOnce({ items: [engagement] });
  const view = render(<SearchResults result={initial} />);
  const card = (await screen.findByRole("link", { name: engagement.name })).closest("article");
  await act(async () => intersect());
  const rows = Array.from(view.container.querySelectorAll(".search-result"));
  expect(rows).toHaveLength(36);
  expect(view.container.querySelector(".partner-placement")).toBe(card);
  expect(deliveries()).toHaveLength(1);

  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision = 1;
  view.rerender(<SearchResults result={initial} />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(1));
  expect(JSON.parse(String(vi.mocked(userRequest).mock.calls[0][1]?.body)).search.results).toEqual({
    articles: hits.map((hit) => hit.id),
  });
  await act(async () => finish({ items: [] }));
  await screen.findByRole("link", { name: engagement.name });
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(JSON.parse(String(vi.mocked(userRequest).mock.calls[1][1]?.body)).search.results).toEqual({
    articles: [...hits, ...later].map((hit) => hit.id),
  });
  expect(Array.from(view.container.querySelectorAll(".search-result"))).toEqual(rows);
  expect(deliveries()).toHaveLength(1);
});

it("uses queued final-page context once after expiry without retrying an empty response", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T12:00:00Z"));
  const later = makeHits(1, 24);
  let attempts = 0;
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements")
        ? {
            items:
              ++attempts === 1
                ? [{ ...engagement, receipt: partnerReceipt({ assetId: engagement.asset_id }) }]
                : [],
          }
        : result(later),
    ),
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SearchResults result={result(hits, "2")} />);
  });
  expect(screen.getByRole("link", { name: engagement.name })).toBeTruthy();
  await act(async () => intersect());
  expect(deliveries()).toHaveLength(1);
  const rows = Array.from(view.container.querySelectorAll(".search-result"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 + 1000);
  });
  expect(deliveries()).toHaveLength(2);
  expect(deliveryContext(1).search.results).toEqual({
    articles: [...hits, ...later].map((hit) => hit.id),
  });
  expect(screen.queryByRole("link", { name: engagement.name })).toBeNull();
  expect(Array.from(view.container.querySelectorAll(".search-result"))).toEqual(rows);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000);
  });
  expect(deliveries()).toHaveLength(2);
});

it("expires a sidebar receipt without replacing organic rows, moving focus or requesting new ads", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T12:00:00Z"));
  vi.mocked(readerRequest).mockImplementation(async () =>
    Response.json({
      items: [{ ...engagement, receipt: partnerReceipt({ assetId: engagement.asset_id }) }],
    }),
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SearchResults result={result(hits)} />);
  });
  const card = screen.getByRole("link", { name: engagement.name }).closest("article")!;
  const stopTracking = vi.mocked(observePartnerPlacement).mock.results.at(-1)!.value;
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
  ).toEqual(hits.map((item) => item.title));
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
  expect(deliveryContext(1).search).toEqual({
    query: "rust",
    results: { articles: nextHits.map((hit) => hit.id) },
  });
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

it("resets pending relevance and paginated result context when search filters change", async () => {
  let finish!: (response: Response) => void;
  const filtered = makeHits(1, 30);
  const later = makeHits(1, 31);
  vi.mocked(readerRequest)
    .mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)))
    .mockImplementation(async (path) =>
      Response.json(String(path).includes("partner-placements") ? { items: [] } : result(later)),
    );
  const view = render(<SearchResults result={result(hits, "2")} />);
  const previous = deliveries()[0][1]!.signal as AbortSignal;
  view.rerender(
    <SearchResults
      result={result(filtered, "2")}
      options={{
        section: "articles",
        content_type: "tutorial",
        sort: "newest",
        date_from: "2026-09-01",
        date_to: "2026-10-01",
      }}
    />,
  );
  await waitFor(() => expect(deliveries()).toHaveLength(2));
  expect(previous.aborted).toBe(true);
  expect(deliveryContext(1).search.results).toEqual({ articles: [filtered[0].id] });
  await act(async () => intersect());
  await waitFor(() => expect(deliveries()).toHaveLength(3));
  expect(deliveryContext(2).search.results).toEqual({
    articles: [...filtered, ...later].map((hit) => hit.id),
  });
  const [path] = vi
    .mocked(readerRequest)
    .mock.calls.find(([path]) => String(path).startsWith("/api/v1/search?"))!;
  expect(Object.fromEntries(new URL(String(path), "https://devfeed.tech").searchParams)).toEqual({
    q: "typescript",
    section: "articles",
    content_types: "tutorial",
    sort: "newest",
    date_from: "2026-09-01",
    date_to: "2026-10-01",
    page: "2",
  });
  await act(async () => finish(Response.json({ items: [engagement] })));
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(
    Array.from(view.container.querySelectorAll(".search-result-link"), (link) => link.textContent),
  ).toEqual([...filtered, ...later].map((hit) => hit.title));
});

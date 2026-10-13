// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchResults } from "@/components/search-results";
import { readerRequest } from "@/lib/reader-runtime";
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
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));
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
let intersect: () => void;
beforeEach(() => {
  localStorage.clear();
  vi.mocked(readerRequest)
    .mockReset()
    .mockImplementation(async () => Response.json({ items: [placement, sponsoredPlacement] }));
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

it("inserts grounded branded products among actual article search results without a banner", async () => {
  const view = render(<SearchResults result={result()} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  const rows = Array.from(
    view.container.querySelector(".search-section-articles .search-result-list")!.children,
  );
  expect(rows).toHaveLength(26);
  expect(rows[4].getAttribute("data-product-id")).toBe(placement.product_id);
  expect(rows[13].getAttribute("data-product-id")).toBe(sponsoredPlacement.product_id);
  expect(
    Array.from(view.container.querySelectorAll(".search-result-link")).map(
      (link) => link.textContent,
    ),
  ).toEqual(hits.map((hit) => hit.title));
  expect(view.container.querySelector(".partner-placements")).toBeNull();
  expect(screen.queryByText(placement.reason)).toBeNull();
  expect(screen.queryByText("Relevant product")).toBeNull();
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    surface: "feed",
    allow_sponsored_fallback: false,
    ad_formats: ["feed", "sponsored_content", "engagement"],
    article_ids: hits.map((hit) => hit.id),
    exclude_product_ids: [],
  });
});

it.each(["topics", "sources", "tags"] as const)(
  "does not insert article products into %s search results",
  async (kind) => {
    const view = render(
      <SearchResults
        result={{
          query: "typescript",
          sections: { [kind]: { items: hits.slice(0, 1), next_cursor: null } },
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
      String(path).includes("partner-placements")
        ? { items: [placement, sponsoredPlacement] }
        : result(hits.slice(4)),
    ),
  );
  const view = render(<SearchResults result={result(hits.slice(0, 4), "2")} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  await act(async () => intersect());
  expect(view.container.querySelectorAll(".search-result")).toHaveLength(24);
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(1);
  const deliveries = vi
    .mocked(readerRequest)
    .mock.calls.filter(([path]) => String(path).includes("partner-placements"));
  expect(deliveries).toHaveLength(1);
  expect(JSON.parse(String(deliveries[0][1]?.body)).article_ids).toEqual(
    hits.slice(0, 4).map((hit) => hit.id),
  );
});

it("restores a paginated organic search hit when its sponsored-content receipt expires", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T12:00:00Z"));
  const promoted = { ...article, topics: [], sponsor_names: [sponsoredPlacement.partner_name] };
  const hit = {
    ...hits[0],
    id: promoted.id,
    title: promoted.title,
    href: `/articles/${promoted.slug}`,
  };
  vi.mocked(readerRequest).mockImplementation(async (path) =>
    Response.json(
      String(path).includes("partner-placements")
        ? {
            items: [
              {
                ...sponsoredPlacement,
                ad_format: "sponsored_content",
                article: promoted,
                url: hit.href,
                receipt: partnerReceipt({ assetId: sponsoredPlacement.asset_id }),
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
  const promotedCard = screen.getByRole("link", { name: promoted.title }).closest("article")!;
  await act(async () => intersect());
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
  const reading = screen.getByRole("link", { name: hits[8].title });
  reading.focus();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 + 1000);
  });

  const organic = screen.getByRole("link", { name: promoted.title }).closest("article")!;
  expect(organic.className).toBe("search-result");
  expect(organic).not.toBe(promotedCard);
  expect(promotedCard.getAttribute("data-suspended")).toBe("true");
  expect(promotedCard.hasAttribute("inert")).toBe(true);
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
  await act(async () => finish(Response.json({ items: [placement, sponsoredPlacement] })));
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(screen.getByRole("heading", { name: "No results for “no-match”" })).toBeTruthy();
});

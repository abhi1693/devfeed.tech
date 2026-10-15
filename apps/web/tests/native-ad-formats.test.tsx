// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect, type ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { PartnerPlacements } from "@/components/partner-placements";
import { HeaderSponsorshipProvider, HeaderSponsorshipSlot } from "@/components/header-sponsorship";
import { readerRequest } from "@/lib/reader-runtime";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import type { PartnerPlacement } from "@/lib/partner-placements";
import { article } from "./fixtures";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false, sessionRevision: 0 }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/feed-preferences", () => ({
  useFeedPreferences: () => ({ view: "cards", loading: false }),
}));
vi.mock("@/lib/reader-runtime", () => ({
  readerRequest: vi.fn(),
  rememberReaderArticles: vi.fn(),
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
const articles = Array.from({ length: 24 }, (_, i) => ({
  ...article,
  id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(i).padStart(12, "0")}`,
  slug: `article-${i}`,
  title: `Article ${i}`,
}));
const promoted = { ...article, topics: [], sponsor_names: ["Example partner"] };
const content: PartnerPlacement = {
  ...sponsoredPlacement,
  ad_format: "sponsored_content",
  article: promoted,
  url: `/articles/${promoted.slug}`,
};
const homepage: PartnerPlacement = { ...placement, ad_format: "header_sponsorship" };
const context = {
  surface: "feed" as const,
  allow_sponsored_fallback: true,
  article_ids: articles.map((item) => item.id),
  ad_formats: [
    "feed",
    "header_sponsorship",
    "sponsored_content",
    "engagement",
  ] as PartnerPlacement["ad_format"][],
};
function Header({ children }: { children: ReactNode }) {
  return (
    <HeaderSponsorshipProvider>
      <header className="topbar">
        <HeaderSponsorshipSlot />
      </header>
      {children}
    </HeaderSponsorshipProvider>
  );
}
const renderFeed = render;
beforeEach(() => {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches: true,
    media: "(min-width: 1280px)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });
  vi.mocked(readerRequest).mockImplementation(async (_, init) =>
    Response.json({
      items: JSON.parse(String(init?.body)).surface === "header" ? [homepage] : [content],
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

it("loads header sponsorship independently of an editorial grid card", async () => {
  const view = render(<ArticleGrid articles={articles} placements={context} />, {
    wrapper: Header,
  });
  const title = await screen.findByRole("link", { name: promoted.title });
  const strip = screen.getByRole("link", { name: homepage.name }).closest("article")!;
  expect(strip.getAttribute("data-ad-format")).toBe("header_sponsorship");
  expect(strip.closest("header")?.className).toBe("topbar");
  expect(view.container.querySelector(".article-placement-feed > .partner-placement")).toBeNull();
  expect(strip.querySelector(".card-image")).toBeNull();
  expect(strip.textContent).toContain(homepage.tagline);
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(2);
  const card = title.closest("article")!;
  expect(card.classList.contains("article-card")).toBe(true);
  expect(card.getAttribute("data-ad-format")).toBe("sponsored_content");
  expect(card.textContent).toContain(promoted.content_type);
  expect(card.querySelector(".card-date")).not.toBeNull();
  expect(title.getAttribute("target")).toBeNull();
  expect(title.getAttribute("rel")).toBe("sponsored");
  expect(card.querySelector("button[aria-label*='Close']")).toBeNull();
  await waitFor(() =>
    expect(observePartnerPlacement).toHaveBeenCalledWith(
      card,
      content.receipt,
      expect.any(Object),
      "a.card-open-link, a.article-read-link",
    ),
  );
});

it("preserves the initial article nodes when header eligibility becomes ready", async () => {
  let initialArticle: Element | null = null;
  function InitialFeed() {
    useLayoutEffect(() => {
      initialArticle = document.querySelector(".article-grid .article-card");
      expect(readerRequest).not.toHaveBeenCalled();
    }, []);
    return <ArticleGrid articles={articles} placements={context} />;
  }
  render(<InitialFeed />, { wrapper: Header });
  await screen.findByRole("link", { name: homepage.name });
  expect(initialArticle).not.toBeNull();
  expect(document.querySelector(".article-grid .article-card")).toBe(initialArticle);
});

it("deduplicates a promoted article when a later organic page returns it", async () => {
  const view = renderFeed(<ArticleGrid articles={articles} placements={context} />);
  await screen.findByRole("link", { name: promoted.title });
  view.rerender(
    <ArticleGrid
      articles={[...articles, promoted]}
      placements={context}
      placementArticleCount={24}
    />,
  );
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
});

it("keeps header delivery stable when the feed has too few articles for a placement", async () => {
  vi.mocked(readerRequest).mockImplementation(async (_, init) =>
    Response.json({ items: JSON.parse(String(init?.body)).surface === "header" ? [homepage] : [] }),
  );
  const view = render(<ArticleGrid articles={articles.slice(0, 4)} placements={context} />, {
    wrapper: Header,
  });
  const sponsor = await screen.findByRole("link", { name: homepage.name });
  const reading = screen.getByRole("link", { name: articles[0].title });
  reading.focus();
  view.rerender(<ArticleGrid articles={articles.slice(0, 3)} placements={context} />);
  await act(async () => {});
  expect(screen.getByRole("link", { name: homepage.name })).toBe(sponsor);
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(
    vi
      .mocked(readerRequest)
      .mock.calls.filter(([, init]) => JSON.parse(String(init?.body)).surface === "header"),
  ).toHaveLength(1);
});

it("restores an organically returned article when its promoted copy expires", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-13T12:00:00Z"));
  vi.mocked(readerRequest).mockResolvedValue(
    Response.json({
      items: [{ ...content, receipt: partnerReceipt({ assetId: content.asset_id }) }],
    }),
  );
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = renderFeed(<ArticleGrid articles={articles} placements={context} />);
  });
  const promotedCard = screen.getByRole("link", { name: promoted.title }).closest("article")!;
  view.rerender(
    <ArticleGrid
      articles={[...articles, promoted]}
      placements={context}
      placementArticleCount={24}
    />,
  );
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
  const reading = screen.getByRole("link", { name: articles[8].title });
  reading.focus();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 + 1000);
  });

  const organic = screen.getByRole("link", { name: promoted.title }).closest("article")!;
  expect(organic).not.toBe(promotedCard);
  expect(organic.getAttribute("data-ad-format")).toBeNull();
  expect(promotedCard.getAttribute("data-suspended")).toBe("true");
  expect(promotedCard.hasAttribute("inert")).toBe(true);
  expect(screen.getByRole("link", { name: articles[8].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("does not replace an already visible article with a paid duplicate", async () => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [content] }));
  const view = renderFeed(
    <ArticleGrid
      articles={[promoted, ...articles]}
      placements={{ ...context, article_ids: [promoted.id] }}
    />,
  );
  await waitFor(() => expect(readerRequest).toHaveBeenCalled());
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(0);
});

it("keeps Engagement out of the article grid even when an upstream response includes it", async () => {
  vi.mocked(readerRequest).mockResolvedValue(
    Response.json({ items: [{ ...placement, ad_format: "engagement" }] }),
  );
  const view = renderFeed(<ArticleGrid articles={articles} placements={context} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalled());
  await act(async () => {});
  expect(
    JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body)).ad_formats,
  ).not.toContain("engagement");
  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(view.container.querySelectorAll(".article-grid > .article-card")).toHaveLength(24);
});

it("rejects a header creative in contextual inventory", async () => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [homepage] }));
  renderFeed(
    <PartnerPlacements
      surface="topic"
      topic_id={placement.product_id}
      ad_formats={["feed", "engagement"]}
    />,
  );
  await waitFor(() => expect(readerRequest).toHaveBeenCalled());
  expect(screen.queryByRole("link", { name: homepage.name })).toBeNull();
});

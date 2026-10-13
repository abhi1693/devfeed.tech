// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleGrid } from "@/components/article-grid";
import { PartnerPlacements } from "@/components/partner-placements";
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
const homepage: PartnerPlacement = { ...placement, ad_format: "homepage_sponsorship" };
const context = {
  surface: "feed" as const,
  allow_sponsored_fallback: true,
  article_ids: articles.map((item) => item.id),
  ad_formats: [
    "feed",
    "homepage_sponsorship",
    "sponsored_content",
    "engagement",
  ] as PartnerPlacement["ad_format"][],
};
beforeEach(() => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [homepage, content] }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("shares two total slots between a compact homepage sponsor and a genuine editorial card", async () => {
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  const title = await screen.findByRole("link", { name: promoted.title });
  const strip = screen.getByRole("link", { name: homepage.name }).closest("article")!;
  expect(strip.getAttribute("data-ad-format")).toBe("homepage_sponsorship");
  expect(strip.nextElementSibling?.className).toBe("article-grid");
  expect(strip.querySelector(".card-image")).toBeNull();
  expect(strip.textContent).not.toContain(homepage.description);
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

it("deduplicates a promoted article when a later organic page returns it", async () => {
  const view = render(<ArticleGrid articles={articles} placements={context} />);
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

it("suspends a loaded homepage sponsor at zero slots and restores its valid delivery without refetching", async () => {
  const stopTracking = vi.fn();
  vi.mocked(observePartnerPlacement).mockReturnValue(stopTracking);
  vi.mocked(readerRequest).mockResolvedValue(
    Response.json({
      items: [{ ...sponsoredPlacement, ad_format: "homepage_sponsorship" }],
    }),
  );
  const view = render(<ArticleGrid articles={articles.slice(0, 4)} placements={context} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  const reading = screen.getByRole("link", { name: articles[0].title });
  reading.focus();

  view.rerender(<ArticleGrid articles={articles.slice(0, 3)} placements={context} />);
  await act(async () => {});

  expect(view.container.querySelector(".partner-placement")).toBeNull();
  expect(stopTracking).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
  expect(readerRequest).toHaveBeenCalledTimes(1);

  view.rerender(<ArticleGrid articles={articles.slice(0, 4)} placements={context} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(observePartnerPlacement).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("link", { name: articles[0].title })).toBe(reading);
  expect(document.activeElement).toBe(reading);
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
    view = render(<ArticleGrid articles={articles} placements={context} />);
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
  const view = render(
    <ArticleGrid
      articles={[promoted, ...articles]}
      placements={{ ...context, article_ids: [promoted.id] }}
    />,
  );
  await waitFor(() => expect(readerRequest).toHaveBeenCalled());
  expect(screen.getAllByRole("link", { name: promoted.title })).toHaveLength(1);
  expect(view.container.querySelectorAll(".partner-placement")).toHaveLength(0);
});

it("renders engagement as a compact contextual cell with signed tracking", async () => {
  vi.mocked(readerRequest).mockResolvedValue(
    Response.json({ items: [{ ...placement, ad_format: "engagement" }] }),
  );
  const view = render(<ArticleGrid articles={articles} placements={context} />);
  const link = await screen.findByRole("link", { name: placement.name });
  expect(link.closest("article")?.className).toContain("partner-placement--compact");
  expect(link.querySelector(".card-image")).toBeNull();
  expect(view.container.querySelectorAll(".article-grid > .partner-placement")).toHaveLength(1);
  expect(screen.getByText("Sponsored")).toBeTruthy();
  expect(link.querySelector(".partner-placement-action")?.textContent).toContain("Explore");
  expect(link.querySelector(".partner-placement-action svg")).toBeTruthy();
  expect(link.querySelector(".partner-placement-destination")?.textContent).toContain(
    "partner.example",
  );
  expect(link.getAttribute("rel")).toBe("sponsored noopener noreferrer");
  expect(link.querySelector("button")).toBeNull();
});

it("rejects a homepage creative outside the unfiltered homepage contract", async () => {
  render(
    <PartnerPlacements
      surface="topic"
      topic_id={placement.product_id}
      ad_formats={["feed", "engagement"]}
    />,
  );
  await waitFor(() => expect(readerRequest).toHaveBeenCalled());
  expect(screen.queryByRole("link", { name: homepage.name })).toBeNull();
});

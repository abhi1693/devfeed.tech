// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ArticlePreviewContent } from "@/components/article-preview-content";
import { ArticleTopicBriefContent } from "@/components/article-topic-brief-content";
import { readerRequest } from "@/lib/reader-runtime";
import { article, source, topic } from "./fixtures";
import { placement } from "./partner-placement-fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false }),
}));
vi.mock("@/components/reader-prompts", () => ({ useReaderPromptCoordinator: () => null }));
vi.mock("@/components/topic-follow", () => ({ TopicFollow: () => null }));
vi.mock("@/components/source-follow", () => ({ SourceFollow: () => null }));
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
}));

beforeEach(() => vi.mocked(readerRequest).mockReset());
afterEach(cleanup);

it.each([
  { hasSource: true, hasTopic: true },
  { hasSource: true, hasTopic: false },
  { hasSource: false, hasTopic: true },
  { hasSource: false, hasTopic: false },
])(
  "places compact Engagement after the available source/topic briefs (%j)",
  async ({ hasSource, hasTopic }) => {
    let finish!: (response: Response) => void;
    vi.mocked(readerRequest).mockImplementationOnce(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const view = render(
      <ArticlePreviewContent
        article={{
          ...article,
          sources: hasSource ? [source] : [],
          topics: hasTopic ? [topic] : [],
        }}
        topicBrief={
          hasTopic ? (
            <ArticleTopicBriefContent topic={topic} articleSlug={article.slug} details={topic} />
          ) : undefined
        }
      />,
    );
    const sidebar = view.container.querySelector(".preview-sidebar")!;
    const briefs = Array.from(sidebar.children);
    expect(briefs).toHaveLength(Number(hasSource) + Number(hasTopic));
    const focused = hasTopic
      ? screen.getByRole("link", { name: topic.name })
      : hasSource
        ? sidebar.querySelector<HTMLAnchorElement>(".source-brief h2 a")
        : null;
    focused?.focus();
    await act(async () =>
      finish(Response.json({ items: [{ ...placement, ad_format: "engagement" }] })),
    );
    const link = screen.getByRole("link", { name: placement.name });
    const card = link.closest("article")!;
    expect(sidebar.lastElementChild).toBe(card.closest(".partner-placements"));
    expect(Array.from(sidebar.children).slice(0, -1)).toEqual(briefs);
    for (const brief of briefs) {
      expect(brief.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    if (focused) expect(document.activeElement).toBe(focused);
    expect(card.getAttribute("data-ad-format")).toBe("engagement");
    expect(card.className).toContain("partner-placement--compact");
    expect(card.querySelector(".card-image")).toBeNull();
    expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject({
      surface: "article",
      article_id: article.id,
      ad_formats: ["engagement"],
    });
    expect(readerRequest).toHaveBeenCalledOnce();
  },
);

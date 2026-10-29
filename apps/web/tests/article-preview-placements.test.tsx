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

it("places Google's preferred-source badge last in the preview sidebar without an ad", () => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [] }));
  const view = render(<ArticlePreviewContent article={article} />);
  const widget = screen.getByTitle("Add DevFeed as a preferred source on Google");
  const actions = screen.getByRole("contentinfo", { name: "Article actions" });
  expect(actions.contains(widget)).toBe(false);
  expect(view.container.querySelector(".preview-sidebar")?.lastElementChild?.contains(widget)).toBe(
    true,
  );
  expect(widget.closest(".preview-preferred-source")).not.toBeNull();
  expect(widget.closest('[aria-label="Article interactions"]')).toBeNull();
  expect(view.container.querySelector(".preview-copy")?.contains(widget)).toBe(false);
  expect(view.container.querySelectorAll("iframe")).toHaveLength(1);
});

it.each([
  { relations: undefined, expected: "nofollow noopener noreferrer" },
  { relations: [], expected: "sponsored noopener noreferrer" },
  { relations: ["external"], expected: "external sponsored noopener noreferrer" },
  { relations: ["ugc"], expected: "ugc sponsored noopener noreferrer" },
  { relations: ["sponsored", "external"], expected: "sponsored external noopener noreferrer" },
] as const)("qualifies sponsored preview links with $relations", ({ relations, expected }) => {
  vi.mocked(readerRequest).mockResolvedValue(Response.json({ items: [] }));
  render(
    <ArticlePreviewContent
      article={{
        ...article,
        sponsor_names: ["Example partner"],
        sponsor_link_rel: relations ? [...relations] : undefined,
      }}
    />,
  );
  expect(
    screen.getByRole("link", { name: `Read ${article.content_type}` }).getAttribute("rel"),
  ).toBe(expected);
});

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
    expect(sidebar.tagName).toBe("DIV");
    expect(sidebar.hasAttribute("data-nosnippet")).toBe(true);
    expect(screen.getByRole("heading", { level: 1 }).closest("[data-nosnippet]")).toBeNull();
    expect(
      view.container.querySelector(".preview-summary")?.closest("[data-nosnippet]"),
    ).toBeNull();
    const badge = sidebar.lastElementChild!;
    expect(badge.classList.contains("preview-preferred-source")).toBe(true);
    expect(badge.contains(screen.getByTitle("Add DevFeed as a preferred source on Google"))).toBe(
      true,
    );
    const briefs = Array.from(sidebar.children).slice(0, -1);
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
    expect(sidebar.lastElementChild).toBe(badge);
    expect(badge.previousElementSibling).toBe(card.closest(".partner-placements"));
    expect(Array.from(sidebar.children).slice(0, -2)).toEqual(briefs);
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

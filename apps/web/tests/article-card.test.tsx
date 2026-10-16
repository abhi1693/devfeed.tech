// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleCard } from "@/components/article-card";
import { EngagementProvider } from "@/components/article-engagement";
import type { Article } from "@/lib/types";
import { userRequest } from "@/lib/user";
import { source, topic } from "./fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({ user: null, loading: false }),
}));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));

const article: Article = {
  id: "article",
  slug: "example",
  canonical_url: "https://publisher.example/story?ref=feed",
  title: "Example article",
  summary: "An example",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: null,
  content_type: "news",
  content_format: "article",
  language: "en",
  published_at: "2026-09-12T12:00:00Z",
  feed_at: "2026-09-12T12:00:00Z",
  tags: [],
  sources: [],
  topics: [],
};

beforeEach(() => {
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path) => {
      const value = { article_id: "article", opens: 0, likes: 0, liked: false };
      return path.startsWith("engagement?") ? [value] : { ...value, opens: 1 };
    });
});
afterEach(cleanup);

it("keeps an article's sponsorship disclosure without an active promotion and preserves real metadata", () => {
  const { container } = render(
    <ArticleCard article={{ ...article, sponsor_names: ["Example partner"] }} />,
  );
  expect(screen.getByText("Sponsored").getAttribute("title")).toBe("Sponsored by Example partner");
  expect(screen.getByRole("link", { name: article.title }).getAttribute("rel")).toBe("nofollow");
  expect(
    screen.getByRole("link", { name: "Read original article in a new tab" }).getAttribute("rel"),
  ).toContain("nofollow");
  expect(screen.getByText("news")).toBeTruthy();
  expect(container.querySelector(".partner-placement")).toBeNull();
});

it("keeps a sponsored article visually stable while its background interaction is paused", () => {
  const content = (active: boolean, visible: boolean) => (
    <ArticleCard
      article={{ ...article, sponsor_names: ["Example partner"] }}
      promotion={{ productId: "product", active, visible, preserveSpace: true }}
    />
  );
  const mounted = render(content(true, true));
  const card = mounted.container.querySelector(".partner-placement")!;
  const link = screen.getByRole("link", { name: article.title });
  mounted.rerender(content(false, true));
  expect(mounted.container.querySelector(".partner-placement")).toBe(card);
  expect(card.querySelector(".card-open-link")).toBe(link);
  expect(card.getAttribute("data-suspended")).toBeNull();
  expect(card.hasAttribute("hidden")).toBe(false);
  expect(card.getAttribute("aria-hidden")).toBe("true");
  expect(card.hasAttribute("inert")).toBe(true);
  mounted.rerender(content(true, true));
  expect(screen.getByRole("link", { name: article.title })).toBe(link);
  mounted.rerender(content(false, false));
  expect(card.getAttribute("data-suspended")).toBe("true");
  expect(card.hasAttribute("hidden")).toBe(false);
  expect(card.hasAttribute("inert")).toBe(true);
});

it("shows the original article as a read overlay and updates the open counter", async () => {
  render(
    <EngagementProvider articleIds={[article.id]}>
      <ArticleCard article={article} />
    </EngagementProvider>,
  );
  const counter = await screen.findByRole("img", { name: "0 clicks to the original article" });
  const link = screen.getByRole("link", { name: "Read original article in a new tab" });
  const bookmark = screen.getByRole("link", { name: "Sign in to save article for later" });
  expect(counter.parentElement?.nextElementSibling).toBe(bookmark.closest(".article-bookmark"));
  expect(link.closest(".card-image")).not.toBeNull();
  expect(screen.queryByRole("button", { name: `Share article: ${article.title}` })).toBeNull();
  expect(link.getAttribute("href")).toBe(
    "https://publisher.example/story?ref=feed&utm_source=devfeed",
  );
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByRole("link", { name: article.title }).getAttribute("href")).toBe(
    "/articles/example",
  );
  expect(screen.getByRole("link", { name: article.title }).textContent).toBe(article.title);
  expect(fireEvent.click(link)).toBe(true);
  await screen.findAllByRole("img", { name: "1 clicks to the original article" });
  await waitFor(() =>
    expect(userRequest).toHaveBeenCalledWith("articles/article/open", {
      method: "POST",
      keepalive: true,
      headers: {},
    }),
  );
  expect(vi.mocked(userRequest).mock.calls.filter(([path]) => path.endsWith("/open"))).toHaveLength(
    1,
  );
});

it("does not expose an unsafe publisher URL as an outbound action", () => {
  render(<ArticleCard article={{ ...article, canonical_url: "javascript:alert(1)" }} />);
  expect(screen.queryByRole("link", { name: "Read original article in a new tab" })).toBeNull();
});

it("reports title activation without treating other card actions as opening the preview", () => {
  const onOpen = vi.fn();
  render(
    <div onClick={(event) => event.preventDefault()}>
      <ArticleCard article={{ ...article, sources: [source], topics: [topic] }} onOpen={onOpen} />
    </div>,
  );
  fireEvent.click(screen.getByRole("link", { name: "Sign in to save article for later" }));
  fireEvent.click(screen.getByRole("link", { name: source.name }));
  fireEvent.click(screen.getByRole("link", { name: `#${topic.name}` }));
  fireEvent.click(screen.getByRole("link", { name: "Read original article in a new tab" }));
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("link", { name: article.title }));
  expect(onOpen).toHaveBeenCalledTimes(1);
});

it("qualifies sponsored card links without changing ordinary article links", () => {
  const view = render(
    <EngagementProvider articleIds={[article.id]}>
      <ArticleCard article={{ ...article, sponsor_names: ["Partner"], sponsor_link_rel: [] }} />
    </EngagementProvider>,
  );
  expect(document.querySelector(".article-read-link")?.getAttribute("rel")).toBe(
    "sponsored noopener noreferrer",
  );
  expect(document.querySelector(".card-open-link")?.getAttribute("rel")).toBe("sponsored");
  view.rerender(
    <EngagementProvider articleIds={[article.id]}>
      <ArticleCard article={{ ...article, sponsor_names: [], sponsor_link_rel: [] }} />
    </EngagementProvider>,
  );
  expect(document.querySelector(".article-read-link")?.getAttribute("rel")).toBe(
    "noopener noreferrer",
  );
  expect(document.querySelector(".card-open-link")?.getAttribute("rel")).toBeNull();
});

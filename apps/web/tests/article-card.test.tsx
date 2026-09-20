// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ArticleCard } from "@/components/article-card";
import { EngagementProvider } from "@/components/article-engagement";
import type { Article } from "@/lib/types";
import { userRequest } from "@/lib/user";

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

it("places bookmark before the original-link control and updates that counter", async () => {
  render(
    <EngagementProvider articleIds={[article.id]}>
      <ArticleCard article={article} />
    </EngagementProvider>,
  );
  const counter = await screen.findByLabelText("0 clicks to the original article");
  const link = screen.getByRole("link", { name: "Open original article in a new tab" });
  const bookmark = screen.getByRole("link", { name: "Sign in to save article for later" });
  expect(counter.parentElement?.nextElementSibling).toBe(bookmark.closest(".article-bookmark"));
  expect(bookmark.closest(".article-bookmark")?.nextElementSibling).toBe(link);
  expect(screen.queryByRole("button", { name: `Share article: ${article.title}` })).toBeNull();
  expect(link.getAttribute("href")).toBe(
    "https://publisher.example/story?ref=feed&utm_source=devfeed",
  );
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(screen.getByRole("link", { name: article.title }).getAttribute("href")).toBe(
    "/articles/example",
  );
  expect(fireEvent.click(link)).toBe(true);
  await screen.findByLabelText("1 clicks to the original article");
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
  expect(screen.queryByRole("link", { name: "Open original article in a new tab" })).toBeNull();
});

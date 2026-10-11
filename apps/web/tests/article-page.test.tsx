import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderToReadableStream } from "react-dom/server";
import ArticlePage, { generateMetadata } from "@/app/articles/[slug]/page";
import { loadArticle } from "@/lib/article";
import { getFeed, getFeedOptions } from "@/lib/api";
import type { Article, FeedOptions, FeedPage } from "@/lib/types";
import type { FeedContentProps } from "@/components/feed-content";

vi.mock("next/headers", () => ({
  headers: async () => new Headers({ "content-security-policy": "script-src 'nonce-test-nonce'" }),
}));
vi.mock("@/lib/article", () => ({ loadArticle: vi.fn() }));
vi.mock("@/lib/api", () => ({ getFeed: vi.fn(), getFeedOptions: vi.fn() }));
vi.mock("@/components/user-shell", () => ({
  UserShell: ({ children }: { children: ReactNode }) => <main>{children}</main>,
}));
vi.mock("@/components/loading-skeleton", () => ({
  LoadingSkeleton: () => <p>Loading background feed</p>,
}));
vi.mock("@/components/article-modal", () => ({
  ArticleModal: ({ children, slug }: { children: ReactNode; slug: string }) => (
    <dialog open data-slug={slug}>
      {children}
    </dialog>
  ),
}));
vi.mock("@/components/article-preview", () => ({
  ArticlePreview: ({ article }: { article: Article }) => <h1>{article.title}</h1>,
}));
vi.mock("@/components/article-unavailable", () => ({
  ArticleUnavailable: ({ children }: { children: ReactNode }) => (
    <section>Article temporarily unavailable{children}</section>
  ),
}));
vi.mock("@/components/retry-feed", () => ({ RetryFeed: () => <button>Try again</button> }));
vi.mock("@/components/feed-content", () => ({
  FeedContent: ({ feed }: FeedContentProps) => (
    <section>
      {feed.status === "fulfilled" ? feed.value.items[0]?.title : "Couldn’t load the feed"}
    </section>
  ),
}));

const article: Article = {
  id: "article-id",
  slug: "requested-article",
  canonical_url: "https://publisher.example/story",
  title: "Requested article",
  summary: "A useful article",
  ai_summary: null,
  ai_description: null,
  image_url: null,
  author: null,
  content_type: "news",
  content_format: "article",
  language: "en",
  published_at: null,
  feed_at: "2026-10-04T00:00:00Z",
  tags: [],
  sources: [],
  topics: [],
};
const params = () => ({ params: Promise.resolve({ slug: article.slug }) });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.mocked(loadArticle).mockReset().mockResolvedValue(article);
  vi.mocked(getFeed).mockReset();
  vi.mocked(getFeedOptions).mockReset();
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://reader.example");
});
afterEach(() => vi.unstubAllEnvs());

it.each([false, true])(
  "streams the requested article while feed and filters are pending (feed fails: %s)",
  async (fails) => {
    const feed = deferred<FeedPage>();
    const filters = deferred<FeedOptions>();
    vi.mocked(getFeed).mockReturnValue(feed.promise);
    vi.mocked(getFeedOptions).mockReturnValue(filters.promise);
    const errors: unknown[] = [];
    const page = await ArticlePage(params());
    const stream = await renderToReadableStream(
      <html>
        {createElement("head")}
        <body>{page}</body>
      </html>,
      {
        onError: (error) => {
          errors.push(error);
        },
      },
    );
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    try {
      const first = await reader.read();
      const shell = decoder.decode(first.value);
      expect(first.done).toBe(false);
      expect(shell).toContain("Requested article");
      expect(shell).toContain('nonce="test-nonce"');
      expect(shell).toContain("dialog.showModal()");
      expect(shell).toContain('data-slug="requested-article"');
      expect(shell).toContain("Loading background feed");
      expect(shell.indexOf("Requested article")).toBeLessThan(
        shell.indexOf("Loading background feed"),
      );
      expect(shell).not.toContain("Background article");
      expect(getFeed).toHaveBeenCalledTimes(1);
      expect(getFeedOptions).toHaveBeenCalledTimes(1);
      if (fails) feed.reject(new Error("Background feed unavailable"));
      else
        feed.resolve({
          items: [{ ...article, title: "Background article" }],
          next_cursor: null,
        });
      filters.resolve({ content_types: [], sources: [] });
      let remainder = "";
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        remainder += decoder.decode(chunk.value);
      }
      expect(remainder).toContain(fails ? "Couldn’t load the feed" : "Background article");
      expect(shell).toContain("Requested article");
      expect(errors).toEqual([]);
    } finally {
      feed.resolve({ items: [], next_cursor: null });
      filters.resolve({ content_types: [], sources: [] });
      await reader.cancel();
    }
  },
);

it("renders an unavailable article with retry without loading the background feed", async () => {
  vi.mocked(loadArticle).mockResolvedValue(null);
  const stream = await renderToReadableStream(await ArticlePage(params()));
  const html = await new Response(stream).text();
  expect(html).toContain("Article temporarily unavailable");
  expect(html).toContain("Try again");
  expect(html).toContain('data-slug="requested-article"');
  expect(getFeed).not.toHaveBeenCalled();
  expect(getFeedOptions).not.toHaveBeenCalled();
});

it("publishes canonical article metadata independently of feed availability", async () => {
  expect(await generateMetadata(params())).toMatchObject({
    title: "Requested article",
    alternates: { canonical: "https://reader.example/articles/requested-article" },
  });
  expect(loadArticle).toHaveBeenCalledWith(article.slug);
  expect(getFeed).not.toHaveBeenCalled();
  expect(getFeedOptions).not.toHaveBeenCalled();
});

it("prevents indexing an unavailable article", async () => {
  vi.mocked(loadArticle).mockResolvedValue(null);
  expect(await generateMetadata(params())).toEqual({
    title: "Article temporarily unavailable",
    robots: { index: false, follow: false },
  });
});

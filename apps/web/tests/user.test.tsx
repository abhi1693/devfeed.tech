// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeedView } from "@/components/feed-view";
const Feed = async ({
  searchParams,
}: {
  searchParams: Promise<import("@/lib/feed-query").SearchParams>;
}) => FeedView({ filters: parseFilters(await searchParams) });
import { ArticleImage } from "@/components/article-image";
import { ArticleCard } from "@/components/article-card";
import { FeedFiltersBar } from "@/components/feed-filters";
import { contentTypes, parseFilters } from "@/lib/feed-query";
import * as api from "@/lib/api";
import { article, source, topic } from "./fixtures";
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@/components/feed-preferences", () => ({
  useFeedPreferences: () => ({
    view: "cards",
    content_types: contentTypes,
    loading: false,
    unavailable: false,
  }),
}));
vi.mock("@/lib/api", () => ({
  getFeed: vi.fn(),
  getTopics: vi.fn(),
  getSources: vi.fn(),
  getFeedOptions: vi.fn(),
  getTopic: vi.fn(),
}));
afterEach(cleanup);
beforeEach(() => {
  vi.mocked(api.getFeed).mockResolvedValue({
    items: [article],
    next_cursor: null,
  });
  vi.mocked(api.getTopics).mockResolvedValue([topic]);
  vi.mocked(api.getSources).mockResolvedValue([source]);
  vi.mocked(api.getFeedOptions).mockResolvedValue({
    content_types: ["tutorial"],
    languages: ["en"],
    sources: [source],
  });
  vi.mocked(api.getTopic).mockResolvedValue(topic);
});
it("renders a clickable card without redundant footer actions", async () => {
  render(await Feed({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "Latest feed" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: article.title })).toBeTruthy();
  expect(screen.getByRole("link", { name: article.title }).getAttribute("href")).toBe(
    `/articles/${article.slug}`,
  );
  expect(screen.queryByText("Quick preview")).toBeNull();
  expect(screen.queryByText("Read original")).toBeNull();
  expect(screen.queryByText(/upvotes|popular|read time/i)).toBeNull();
});
it("distinguishes an empty feed from a feed outage", async () => {
  vi.mocked(api.getFeed).mockResolvedValue({ items: [], next_cursor: null });
  const view = render(await Feed({ searchParams: Promise.resolve({}) }));
  expect(
    screen.getByRole("heading", {
      name: "No articles yet",
    }),
  ).toBeTruthy();
  view.unmount();
  vi.mocked(api.getFeed).mockRejectedValue(new Error("offline"));
  render(await Feed({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "Couldn’t load the feed" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(refresh).toHaveBeenCalledOnce();
});
it("shows an actionable filtered empty state", async () => {
  vi.mocked(api.getFeed).mockResolvedValue({ items: [], next_cursor: null });
  render(await Feed({ searchParams: Promise.resolve({ q: "unmatched" }) }));
  expect(screen.getByText("No articles match these filters")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Clear filters" }).getAttribute("href")).toBe("/");
});
it("keeps article content usable when topic or source lists fail", async () => {
  vi.mocked(api.getTopics).mockRejectedValue(new Error("offline"));
  vi.mocked(api.getFeedOptions).mockRejectedValue(new Error("offline"));
  render(await Feed({ searchParams: Promise.resolve({}) }));
  expect(screen.getByText(article.title)).toBeTruthy();
});
it("paginates without dropping filters and offers a fresh first page", async () => {
  vi.mocked(api.getFeed).mockResolvedValue({
    items: [article],
    next_cursor: "next+cursor",
  });
  render(
    await Feed({
      searchParams: Promise.resolve({
        q: "type",
        topic: "typescript",
        cursor: "old",
      }),
    }),
  );
  const url = new URL(
    screen.getByRole("link", { name: "More articles" }).getAttribute("href")!,
    "http://localhost",
  );
  expect(url.searchParams.get("cursor")).toBe("next+cursor");
  expect(url.pathname).toBe("/topics/typescript");
  expect(url.searchParams.get("q")).toBe("type");
  expect(screen.getByRole("link", { name: "Back to latest" }).getAttribute("href")).not.toContain(
    "cursor",
  );
});
it("filter changes preserve search and clear stale pagination", () => {
  render(
    <FeedFiltersBar
      filters={parseFilters({
        q: "routing",
        topic: "typescript",
        cursor: "old",
      })}
      sources={[source]}
    />,
  );
  fireEvent.click(screen.getByText("Filters"));
  expect(screen.getByRole("combobox", { name: "Source" })).toBeTruthy();
  const link = screen.getByRole("link", { name: "Tutorials" }).getAttribute("href")!;
  expect(link).toContain("q=routing");
  expect(link).not.toContain("cursor");
});
it("escapes article content and omits unsafe publisher URLs", () => {
  render(
    <ArticleCard
      article={{
        ...article,
        title: "<script>alert(1)</script>",
        canonical_url: "javascript:alert(1)",
      }}
    />,
  );
  expect(screen.getByText("<script>alert(1)</script>")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /at the original source/ })).toBeNull();
  expect(document.querySelector("script")).toBeNull();
});

it("keeps a useful thumbnail when a publisher image fails", () => {
  const view = render(<ArticleImage src="https://example.com/missing.jpg" label="TypeScript" />);
  fireEvent.error(view.container.querySelector("img")!);
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByText("TypeScript")).toBeTruthy();
});
it("preserves a selected source and language outside the filter shortlist", () => {
  render(
    <FeedFiltersBar
      filters={parseFilters({ source_id: source.id, language: "en-gb" })}
      sources={[]}
    />,
  );
  expect((document.querySelector('select[name="source_id"]') as HTMLSelectElement).value).toBe(
    source.id,
  );
  expect((document.querySelector('select[name="language"]') as HTMLSelectElement).value).toBe(
    "en-gb",
  );
});

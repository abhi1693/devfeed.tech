// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ArticleViewSettings } from "@/components/article-view-settings";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { ArticleGrid } from "@/components/article-grid";
import { InfiniteFeed } from "@/components/infinite-feed";
import { ArticleNavigationProvider, useArticleNavigation } from "@/components/article-navigation";
import { parseFilters } from "@/lib/feed-query";
import { article } from "./fixtures";

vi.mock("@/components/user-account", () => ({
  useUser: () => ({
    user: { user_id: "reader", csrf_token: "csrf" },
    loading: false,
    sessionRevision: 1,
    refreshProfile: vi.fn(),
  }),
}));
vi.mock("@/components/infinite-scroll", () => ({
  InfiniteScroll: ({
    children,
    hasMore,
    onLoadMore,
  }: {
    children: ReactNode;
    hasMore: boolean;
    onLoadMore: () => Promise<unknown>;
  }) => (
    <>
      {children}
      {hasMore && <button onClick={() => void onLoadMore()}>Load another page</button>}
    </>
  ),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function Sequence() {
  const { sequence } = useArticleNavigation();
  return <output aria-label="Reading sequence">{sequence?.slugs.join(",")}</output>;
}

it("applies Appearance layout globally while retaining pages, reading sequence, and engagement", async () => {
  const second = { ...article, id: "second", slug: "second", title: "Second page article" };
  const other = { ...article, id: "other", slug: "other", title: "Another feed article" };
  const firstEngagement = {
    article_id: article.id,
    liked: false,
    likes: 2,
    opens: 7,
    bookmarked: false,
  };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("settings/feed"))
      return Response.json(
        init?.method === "PUT"
          ? JSON.parse(String(init.body))
          : { view: "cards", content_types: ["news"], languages: ["en"] },
      );
    if (url.pathname === "/api/v1/feed")
      return Response.json({ items: [second], next_cursor: null });
    if (url.pathname.endsWith("engagement"))
      return Response.json(
        url.searchParams.getAll("article_id").map((id) => ({ ...firstEngagement, article_id: id })),
      );
    if (url.pathname.endsWith("/bookmark"))
      return Response.json({ article_id: article.id, bookmarked: true });
    if (url.pathname.endsWith("/open")) return Response.json({ ...firstEngagement, opens: 8 });
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ArticleViewSettings />
      <ArticleNavigationProvider>
        <InfiniteFeed
          initialPage={{ items: [article], next_cursor: "next-page" }}
          filters={parseFilters({ q: "python", content_type: "news", sort: "most_liked" })}
        />
        <Sequence />
      </ArticleNavigationProvider>
      <ArticleGrid articles={[other]} />
    </FeedPreferencesProvider>,
  );
  const list = await screen.findByRole("radio", { name: "List" });
  await waitFor(() =>
    expect(
      (screen.getAllByRole("button", { name: "Save article for later" })[0] as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Load another page" }));
  await screen.findByRole("heading", { name: second.title });
  const sequence = screen.getByLabelText("Reading sequence").textContent;
  fireEvent.click(screen.getAllByRole("button", { name: "Save article for later" })[0]);
  await screen.findByRole("button", { name: "Remove bookmark" });
  const original = screen.getAllByRole("link", { name: "Read original article in a new tab" })[0];
  original.addEventListener("click", (event) => event.preventDefault(), { once: true });
  fireEvent.click(original);
  await screen.findAllByRole("img", { name: "8 clicks to the original article" });
  await act(async () => fireEvent.click(list));
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(screen.getByRole("link", { name: second.title })).toBeTruthy();
  expect(screen.getByRole("link", { name: other.title })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove bookmark" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sequence);
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Grid" })));
  expect(
    screen.getAllByRole("img", { name: "8 clicks to the original article" }).length,
  ).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Remove bookmark" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: second.title })).toBeTruthy();
  const feedRequests = fetcher.mock.calls.filter(([input]) =>
    String(input).startsWith("/api/v1/feed?"),
  );
  expect(feedRequests).toHaveLength(1);
  expect(
    Object.fromEntries(new URL(String(feedRequests[0][0]), "http://localhost").searchParams),
  ).toMatchObject({
    q: "python",
    content_type: "news",
    sort: "most_liked",
    cursor: "next-page",
  });
});

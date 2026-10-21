// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ArticleViewOptions, ArticleViewSettings } from "@/components/article-view-settings";
import { ScopedFeedPreferences } from "@/components/scoped-feed-preferences";
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

it("edits the selected feed in Appearance and restores another feed's independent layout", async () => {
  const saved = new Map([
    ["my_feed", "columns"],
    ["latest", "stacked"],
  ]);
  const legacy = { view: "columns", columns: [], content_types: ["news"], languages: ["en"] };
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("settings/feed")) return Response.json(legacy);
    if (url.pathname.endsWith("settings/feed-view")) {
      const scope = url.searchParams.get("feed")!;
      if (init?.method === "PUT") saved.set(scope, JSON.parse(String(init.body)).view);
      return Response.json({ view: saved.get(scope) ?? "cards" });
    }
    throw new Error(`Unexpected request: ${url.href}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ArticleViewSettings />
    </FeedPreferencesProvider>,
  );
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Columns" }) as HTMLInputElement).checked).toBe(true),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Feed" }), { target: { value: "latest" } });
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Stacked" }) as HTMLInputElement).checked).toBe(true),
  );
  expect((screen.getByRole("radio", { name: "Columns" }) as HTMLInputElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: "List" }));
  await waitFor(() => expect(saved.get("latest")).toBe("compact"));
  fireEvent.change(screen.getByRole("combobox", { name: "Feed" }), {
    target: { value: "my_feed" },
  });
  await waitFor(() =>
    expect((screen.getByRole("radio", { name: "Columns" }) as HTMLInputElement).checked).toBe(true),
  );
  expect(saved.get("my_feed")).toBe("columns");
  expect(
    fetcher.mock.calls.filter(
      ([input, init]) =>
        new URL(String(input), "http://localhost").pathname.endsWith("settings/feed") &&
        init?.method === "PUT",
    ),
  ).toHaveLength(0);
});

function Sequence() {
  const { sequence } = useArticleNavigation();
  return <output aria-label="Reading sequence">{sequence?.slugs.join(",")}</output>;
}

it("applies Grid, List, and Stacked while retaining pages, reading sequence, and engagement", async () => {
  const second = { ...article, id: "second", slug: "second", title: "Second page article" };
  const other = { ...article, id: "other", slug: "other", title: "Another feed article" };
  const firstEngagement = {
    article_id: article.id,
    liked: false,
    likes: 2,
    opens: 7,
    bookmarked: false,
  };
  const columns = [
    { id: "11111111-1111-4111-8111-111111111111", feed: { kind: "latest" } },
    { id: "33333333-3333-4333-8333-333333333333", feed: { kind: "recommended" } },
  ];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("settings/feed-view"))
      return Response.json(
        init?.method === "PUT" ? JSON.parse(String(init.body)) : { view: "cards" },
      );
    if (url.pathname.endsWith("settings/feed"))
      return Response.json(
        init?.method === "PUT"
          ? { ...JSON.parse(String(init.body)), columns }
          : { view: "cards", columns, content_types: ["news"], languages: ["en"] },
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
    if (url.pathname.endsWith("/like"))
      return Response.json({ ...firstEngagement, liked: true, likes: 3, opens: 8 });
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <FeedPreferencesProvider>
      <ScopedFeedPreferences scope="my_feed">
        <ArticleViewOptions />
        <ArticleNavigationProvider>
          <InfiniteFeed
            initialPage={{ items: [article], next_cursor: "next-page" }}
            filters={parseFilters({ q: "python", content_type: "news", sort: "most_liked" })}
          />
          <Sequence />
        </ArticleNavigationProvider>
      </ScopedFeedPreferences>
      <ScopedFeedPreferences scope="latest">
        <ArticleGrid articles={[other]} />
      </ScopedFeedPreferences>
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
  fireEvent.click(screen.getAllByRole("button", { name: "Like article, 2 likes" })[0]);
  await screen.findByRole("button", { name: "Unlike article, 3 likes" });
  const engagementReads = fetcher.mock.calls.filter(([input]) =>
    new URL(String(input), "http://localhost").pathname.endsWith("engagement"),
  ).length;
  const expectedOrder = [article.title, second.title, other.title];
  const articleOrder = () =>
    screen
      .getAllByRole("link")
      .map((link) => link.textContent)
      .filter((title) => expectedOrder.includes(title!));
  await act(async () => fireEvent.click(list));
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getByRole("link", { name: other.title }).closest(".article-card")).toBeTruthy();
  expect(screen.getByRole("link", { name: second.title })).toBeTruthy();
  expect(screen.getByRole("link", { name: other.title })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove bookmark" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sequence);
  expect(articleOrder()).toEqual(expectedOrder);
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Stacked" })));
  expect(screen.getAllByRole("region", { name: "Articles in stacked view" })).toHaveLength(1);
  expect(screen.queryByRole("table")).toBeNull();
  expect(articleOrder()).toEqual(expectedOrder);
  expect(
    screen.getByRole("button", { name: "Unlike article, 3 likes" }).getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.getByRole("button", { name: "Remove bookmark" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
  expect(screen.getByRole("img", { name: "8 clicks to the original article" })).toBeTruthy();
  expect(screen.getByLabelText("Reading sequence").textContent).toBe(sequence);
  await act(async () => fireEvent.click(screen.getByRole("radio", { name: "Grid" })));
  expect(
    screen.getAllByRole("img", { name: "8 clicks to the original article" }).length,
  ).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Remove bookmark" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: second.title })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Unlike article, 3 likes" })).toBeTruthy();
  expect(articleOrder()).toEqual(expectedOrder);
  expect(
    fetcher.mock.calls.filter(([input]) =>
      new URL(String(input), "http://localhost").pathname.endsWith("engagement"),
    ),
  ).toHaveLength(engagementReads);
  expect(
    fetcher.mock.calls
      .filter(
        ([input, init]) =>
          String(input).endsWith("settings/feed-view?feed=my_feed") && init?.method === "PUT",
      )
      .map(([, init]) => JSON.parse(String(init?.body))),
  ).toEqual(["compact", "stacked", "cards"].map((view) => ({ view })));
  expect(
    fetcher.mock.calls.filter(
      ([input, init]) => String(input).endsWith("settings/feed") && init?.method === "PUT",
    ),
  ).toHaveLength(0);
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

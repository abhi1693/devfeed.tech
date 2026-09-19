// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UserProvider } from "@/components/user-account";
import { SourceFollowsProvider } from "@/components/source-follow";
import { SearchFailure, SearchResults } from "@/components/search-results";
import { searchKinds, type SearchHit, type SearchResponse } from "@/lib/search";

const { refresh } = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const fetcher = vi.fn();
const intersections = new Map<string, () => void>();
const item = (id: string): SearchHit => ({
  id,
  title: id,
  description: "A concise description",
  href: `/articles/${id}`,
  image_url: null,
  label: "article",
  published_at: null,
});
function result(): SearchResponse {
  return {
    query: "kubernetes",
    sections: Object.fromEntries(
      searchKinds.map((kind) => [
        kind,
        {
          items: [
            { ...item(kind), href: kind === "tags" ? "/tags/kubernetes" : `/${kind}/kubernetes` },
          ],
          next_cursor: "2",
        },
      ]),
    ),
  };
}
beforeEach(() => {
  fetcher.mockReset();
  intersections.clear();
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(private callback: IntersectionObserverCallback) {}
      observe(element: Element) {
        const section =
          element.closest("section")!.getAttribute("aria-labelledby") ?? "search-articles";
        intersections.set(section, () =>
          this.callback(
            [{ isIntersecting: true } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          ),
        );
      }
      disconnect() {}
    },
  );
  refresh.mockReset();
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows articles first and waits for a section boundary before fetching", () => {
  render(<SearchResults result={result()} />);
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Topics",
    "Sources",
    "Tags",
  ]);
  expect(screen.getByRole("link", { name: /tags/ }).getAttribute("href")).toBe("/tags/kubernetes");
  expect(
    document.querySelector(".search-result-meta .content-type")?.getAttribute("data-content-type"),
  ).toBe("article");
  expect(fetcher).not.toHaveBeenCalled();
});

it("tracks privacy-bounded query, impressions, zero-results, and clicks", async () => {
  const spy = vi.spyOn(window, "dispatchEvent");
  render(<SearchResults result={result()} />);
  await waitFor(() => expect(spy).toHaveBeenCalled());
  const details = spy.mock.calls.map(([event]) => (event as CustomEvent).detail);
  expect(details).toContainEqual({
    name: "search_query",
    params: { query_length: 10, word_count: 1, section: "all", result_count: 4 },
  });
  expect(details).toContainEqual({
    name: "search_impression",
    params: { result_kind: "articles", result_id: "articles", position: 1 },
  });
  fireEvent.click(screen.getByRole("link", { name: "articles" }));
  expect(spy.mock.calls.map(([event]) => (event as CustomEvent).detail)).toContainEqual({
    name: "search_click",
    params: { result_kind: "articles", result_id: "articles", position: 1 },
  });
  expect(spy.mock.calls.map(([event]) => (event as CustomEvent).detail)).toContainEqual({
    name: "search_conversion",
    params: { result_kind: "articles", result_id: "articles", position: 1 },
  });

  spy.mockClear();
  render(
    <SearchResults
      result={{ query: "missing", sections: { articles: { items: [], next_cursor: null } } }}
    />,
  );
  await waitFor(() =>
    expect(spy.mock.calls.map(([event]) => (event as CustomEvent).detail)).toContainEqual({
      name: "search_zero_result",
      params: { query_length: 7, word_count: 1, section: "all" },
    }),
  );
});

it("loads only the requested section and de-duplicates hits", async () => {
  fetcher.mockResolvedValue(
    Response.json({
      query: "kubernetes",
      sections: {
        topics: { items: [item("topics"), item("another-topic")], next_cursor: null },
      },
    }),
  );
  render(<SearchResults result={result()} />);
  expect(screen.queryByRole("button", { name: "More topics" })).toBeNull();
  await act(async () => intersections.get("search-topics")!());
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/search?q=kubernetes&section=topics&page=2");
  expect(screen.getAllByRole("link", { name: "topics" })).toHaveLength(1);
  expect(screen.getByRole("link", { name: "another-topic" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "More topics" })).toBeNull();
  expect(screen.queryByRole("button", { name: "More articles" })).toBeNull();
  expect(intersections.has("search-articles")).toBe(true);
});

it("retains results on failure and cancels a retry when the tab loses focus", async () => {
  fetcher
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockImplementationOnce(() => new Promise(() => {}));
  render(<SearchResults result={result()} />);
  await act(async () => intersections.get("search-articles")!());
  expect(screen.getByRole("link", { name: "articles" })).toBeDefined();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
  const signal = fetcher.mock.calls[1][1].signal;
  vi.mocked(document.hasFocus).mockReturnValue(false);
  await act(async () => window.dispatchEvent(new Event("blur")));
  expect(signal.aborted).toBe(true);
});

it("keeps pagination available when stale hits on the first page were withdrawn", () => {
  const initial = result();
  for (const section of Object.values(initial.sections)) section.items = [];
  render(<SearchResults result={initial} />);
  expect(screen.queryByRole("button", { name: "More articles" })).toBeNull();
  expect(intersections.has("search-articles")).toBe(true);
  expect(screen.queryByText(/No results for/)).toBeNull();
});

it("retries the server search after an outage", () => {
  render(<SearchFailure />);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(refresh).toHaveBeenCalledOnce();
});

it.each(searchKinds)(
  "automatically appends %s without duplicates and stops at the last page",
  async (kind) => {
    let finish!: (response: Response) => void;
    fetcher.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
    );
    render(<SearchResults result={result()} />);
    const intersect = intersections.get(`search-${kind}`)!;
    await act(async () => {
      intersect();
      intersect();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/search?q=kubernetes&section=${kind}&page=2`);
    await act(async () =>
      finish(
        Response.json({
          query: "kubernetes",
          sections: { [kind]: { items: [item(kind), item("next-result")], next_cursor: null } },
        }),
      ),
    );
    expect(screen.getAllByRole("link", { name: kind })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "next-result" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: `More ${kind}` })).toBeNull();
    await act(async () => intersect());
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it("cancels pagination and resets results when the query changes", async () => {
  let finish!: (response: Response) => void;
  fetcher.mockReturnValueOnce(
    new Promise<Response>((resolve) => {
      finish = resolve;
    }),
  );
  const view = render(<SearchResults result={result()} />);
  await act(async () => intersections.get("search-articles")!());
  const signal = fetcher.mock.calls[0][1].signal;
  view.rerender(
    <SearchResults
      result={{
        query: "rust",
        sections: { articles: { items: [item("rust-result")], next_cursor: null } },
      }}
    />,
  );
  expect(signal.aborted).toBe(true);
  await act(async () =>
    finish(
      Response.json({
        query: "kubernetes",
        sections: { articles: { items: [item("stale-result")], next_cursor: null } },
      }),
    ),
  );
  expect(screen.getByRole("link", { name: "rust-result" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "stale-result" })).toBeNull();
  expect(screen.queryByRole("link", { name: "articles" })).toBeNull();
});

it("shows lazy article thumbnails and keeps a stable fallback when an image fails", () => {
  const data = result();
  data.sections.articles!.items[0].image_url = "https://images.example/cover.jpg";
  const { container } = render(<SearchResults result={data} />);
  const thumbnail = container.querySelector<HTMLDivElement>(".search-result-thumbnail")!;
  const image = thumbnail.querySelector("img")!;
  expect(image.getAttribute("src")).toBe("https://images.example/cover.jpg");
  expect(image.getAttribute("loading")).toBe("lazy");
  const row = thumbnail.closest("article")!;
  expect(row.querySelectorAll("a")).toHaveLength(1);
  expect(row.querySelector(".search-result-link")?.getAttribute("href")).toBe(
    "/articles/kubernetes",
  );
  expect(container.querySelector(".search-related-results .search-result-thumbnail")).toBeNull();
  fireEvent.error(image);
  expect(thumbnail.querySelector(".image-placeholder")).toBeTruthy();
  expect(screen.getByRole("link", { name: "articles" })).toBeTruthy();
});

it("preserves filters and sorting when loading another search page", async () => {
  fetcher.mockResolvedValue(
    Response.json({
      query: "kubernetes",
      sections: { articles: { items: [item("filtered-next")], next_cursor: null } },
    }),
  );
  render(
    <SearchResults
      result={result()}
      options={{
        section: "articles",
        sort: "oldest",
        date_from: "2025-01-01",
        date_to: "2026-09-01",
      }}
    />,
  );
  expect(screen.queryByRole("heading", { name: "Topics" })).toBeNull();
  await act(async () => intersections.get("search-articles")!());
  const url = new URL(fetcher.mock.calls[0][0], "http://localhost");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    q: "kubernetes",
    section: "articles",
    page: "2",
    sort: "oldest",
    date_from: "2025-01-01",
    date_to: "2026-09-01",
  });
  expect(screen.getByRole("link", { name: "filtered-next" })).toBeTruthy();
});

it("follows and unfollows topics and sources without nesting controls inside result links", async () => {
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("auth/me")) return Response.json({ user_id: "reader", csrf_token: "csrf" });
    if (init?.method === "PUT") return Response.json(JSON.parse(String(init.body)));
    return Response.json({ topic_ids: [], source_ids: [] });
  });
  render(
    <UserProvider>
      <SourceFollowsProvider>
        <SearchResults result={result()} />
      </SourceFollowsProvider>
    </UserProvider>,
  );
  for (const kind of ["topics", "sources"]) {
    const group = () => within(screen.getByRole("group", { name: `Follow ${kind}` }));
    await waitFor(() =>
      expect(group().getByRole("button", { name: "Follow" })).toHaveProperty("disabled", false),
    );
    const button = group().getByRole("button", { name: "Follow" });
    expect(button.closest("a")).toBeNull();
    fireEvent.click(button);
    const followed = await group().findByRole("button", { name: "Following" });
    expect(followed.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(followed);
    await group().findByRole("button", { name: "Follow" });
    const writes = fetcher.mock.calls.filter(
      ([url, init]) => url.endsWith(`preferences/${kind}/${kind}`) && init?.method === "PUT",
    );
    expect(writes.map(([, init]) => JSON.parse(init.body))).toEqual([
      { followed: true },
      { followed: false },
    ]);
    expect(writes[0][1].headers["X-CSRF-Token"]).toBe("csrf");
  }
});

it("returns anonymous followers to the same search and filters", async () => {
  fetcher.mockResolvedValue(Response.json(null));
  render(
    <UserProvider>
      <SourceFollowsProvider>
        <SearchResults
          result={result()}
          options={{ sort: "newest", section: "", date_from: "", date_to: "" }}
        />
      </SourceFollowsProvider>
    </UserProvider>,
  );
  const links = await screen.findAllByRole("link", { name: "Follow" });
  expect(links).toHaveLength(2);
  for (const link of links) {
    const destination = new URL(link.getAttribute("href")!, "https://devfeed.tech");
    expect(destination.pathname).toBe("/api/v1/user/auth/login");
    const returnTo = new URL(destination.searchParams.get("return_to")!, "https://devfeed.tech");
    expect(returnTo.pathname).toBe("/search");
    expect(returnTo.searchParams.get("q")).toBe("kubernetes");
    expect(returnTo.searchParams.get("sort")).toBe("newest");
  }
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InfiniteFeed } from "@/components/infinite-feed";
import { parseFilters } from "@/lib/feed-query";
import type { Article } from "@/lib/types";
import { article } from "./fixtures";

vi.mock("@/components/article-grid", () => ({
  ArticleGrid: ({ articles }: { articles: Article[] }) => (
    <div data-testid="article-grid">
      {articles.map((item) => (
        <p key={item.id}>{item.title}</p>
      ))}
    </div>
  ),
}));
let intersect: () => void;
let disconnect: ReturnType<typeof vi.fn>;
const nextArticle = { ...article, id: "second", title: "Second article" };
const initialPage = { items: [article], next_cursor: "next+/=" };
const filters = parseFilters({
  q: "python",
  topic: "python",
  content_type: "tutorial",
  language: "en",
});
const fetcher = vi.fn();
beforeEach(() => {
  fetcher.mockReset();
  disconnect = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        intersect = () =>
          callback(
            [{ isIntersecting: true } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          );
      }
      observe() {}
      disconnect = disconnect;
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("cancels background pagination and resumes observing the same cursor on return", async () => {
  fetcher
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue(Response.json({ items: [nextArticle], next_cursor: null }));
  render(<InfiniteFeed initialPage={initialPage} filters={filters} />);
  await act(async () => intersect());
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  vi.mocked(document.hasFocus).mockReturnValue(false);
  await act(async () => {
    window.dispatchEvent(new Event("blur"));
  });
  expect(signal.aborted).toBe(true);
  await act(async () => intersect());
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByText(article.title)).toBeTruthy();
  vi.mocked(document.hasFocus).mockReturnValue(true);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await act(async () => intersect());
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.getByText(nextArticle.title)).toBeTruthy();
});

it("loads once per cursor, preserves filters, appends without duplicates, and stops at the end", async () => {
  let resolve!: (response: Response) => void;
  fetcher.mockReturnValue(
    new Promise<Response>((done) => {
      resolve = done;
    }),
  );
  render(<InfiniteFeed initialPage={initialPage} filters={filters} />);
  expect(screen.queryByRole("link", { name: "More articles" })).toBeNull();
  expect(screen.queryByRole("button", { name: "More articles" })).toBeNull();
  await act(async () => {
    intersect();
    intersect();
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  const url = new URL(fetcher.mock.calls[0][0], "http://localhost");
  expect(url.pathname).toBe("/api/v1/feed");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    q: "python",
    topic: "python",
    content_type: "tutorial",
    cursor: "next+/=",
  });
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Loading more articles…");
  await act(async () =>
    resolve(Response.json({ items: [article, nextArticle, nextArticle], next_cursor: null })),
  );
  expect(screen.getAllByText(article.title)).toHaveLength(1);
  expect(screen.getAllByText(nextArticle.title)).toHaveLength(1);
  expect(screen.getAllByTestId("article-grid")).toHaveLength(1);
  expect(screen.getByText("You’re all caught up.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "More articles" })).toBeNull();
  expect(disconnect).toHaveBeenCalled();
});

it("keeps loaded cards on failure and retries only when requested", async () => {
  fetcher
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ items: [nextArticle], next_cursor: null }));
  render(<InfiniteFeed initialPage={initialPage} filters={filters} />);
  await act(async () => intersect());
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(screen.getByText("Couldn’t load more articles.")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(1);
  await act(async () => fireEvent.click(screen.getByRole("link", { name: "Try again" })));
  expect(screen.getByText(nextArticle.title)).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("loads near the scroll boundary without IntersectionObserver or a More articles control", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  const bounds = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  bounds.mockReturnValue({ top: 5000, bottom: 5050 } as DOMRect);
  fetcher.mockResolvedValue(Response.json({ items: [nextArticle], next_cursor: null }));
  render(<InfiniteFeed initialPage={initialPage} filters={filters} />);
  expect(screen.queryByRole("link", { name: "More articles" })).toBeNull();
  expect(screen.queryByRole("button", { name: "More articles" })).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
  bounds.mockReturnValue({ top: 100, bottom: 150 } as DOMRect);
  await act(async () => fireEvent.scroll(window));
  expect(screen.getByText(nextArticle.title)).toBeTruthy();
  expect(fetcher).toHaveBeenCalledOnce();
  bounds.mockRestore();
});

it("cancels in-flight work when filters change and starts from the new first page", async () => {
  fetcher.mockImplementation(() => new Promise(() => {}));
  const view = render(<InfiniteFeed key="python" initialPage={initialPage} filters={filters} />);
  await act(async () => intersect());
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  view.rerender(
    <InfiniteFeed
      key="news"
      initialPage={{ items: [nextArticle], next_cursor: null }}
      filters={parseFilters({ content_type: "news" })}
    />,
  );
  expect(signal.aborted).toBe(true);
  expect(screen.queryByText(article.title)).toBeNull();
  expect(screen.getByText(nextArticle.title)).toBeTruthy();
});

it("stops a repeated cursor from creating an endless request loop", async () => {
  fetcher.mockResolvedValue(Response.json(initialPage));
  render(<InfiniteFeed initialPage={initialPage} filters={filters} />);
  await act(async () => intersect());
  expect(screen.getByText("You’re all caught up.")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([409, 200])(
  "preserves recommendations and offers a restart if the generation changes (%s)",
  async (status) => {
    fetcher.mockResolvedValue(
      Response.json({ status: "refreshing", items: [], next_cursor: null }, { status }),
    );
    render(<InfiniteFeed initialPage={initialPage} personal />);
    await act(async () => intersect());
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/user/feed?limit=24&cursor=next%2B%2F%3D");
    expect(screen.getByText(article.title)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Show updated feed" }).getAttribute("href")).toBe("/");
    expect(screen.queryByText("You’re all caught up.")).toBeNull();
  },
);

it("continues Trending through its own endpoint without a More articles control", async () => {
  fetcher.mockResolvedValue(Response.json({ items: [nextArticle], next_cursor: null }));
  render(<InfiniteFeed initialPage={{ ...initialPage, next_cursor: "24" }} trending />);
  expect(screen.queryByRole("link", { name: "More articles" })).toBeNull();
  await act(async () => intersect());
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/user/trending?limit=24&cursor=24");
  expect(screen.getByText(nextArticle.title)).toBeTruthy();
  expect(screen.getByText("You’re all caught up.")).toBeTruthy();
});

it("retains Latest results and offers a filtered restart when its snapshot expires", async () => {
  fetcher.mockResolvedValue(Response.json({}, { status: 409 }));
  render(
    <InfiniteFeed
      initialPage={initialPage}
      filters={{
        ...filters,
        q: "",
        topic: "",
        tag: "",
        source_id: "",
        content_type: "news",
      }}
    />,
  );
  await act(async () => intersect());
  expect(screen.getByText(article.title)).toBeTruthy();
  expect(screen.getByRole("link", { name: "Show updated feed" }).getAttribute("href")).toBe(
    "/news",
  );
});

it.each(["personal", "bookmarks"] as const)(
  "automatically appends %s articles without a More control",
  async (kind) => {
    fetcher.mockResolvedValue(Response.json({ items: [nextArticle], next_cursor: null }));
    render(<InfiniteFeed initialPage={initialPage} {...{ [kind]: true }} />);
    expect(screen.queryByRole("link", { name: "More articles" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More articles" })).toBeNull();
    await act(async () => intersect());
    expect(fetcher.mock.calls[0][0]).toBe(
      `/api/v1/user/${kind === "personal" ? "feed" : "bookmarks"}?limit=24&cursor=next%2B%2F%3D`,
    );
    expect(screen.getByText(nextArticle.title)).toBeTruthy();
  },
);

it("preserves personal sort and source/type filters through pagination and recovery", async () => {
  fetcher.mockResolvedValue(Response.json({}, { status: 409 }));
  const sourceId = "11111111-1111-4111-8111-111111111111";
  render(
    <InfiniteFeed
      initialPage={initialPage}
      personal
      filters={parseFilters({ sort: "most_liked", content_type: "news", source_id: sourceId })}
    />,
  );
  await act(async () => intersect());
  const url = new URL(fetcher.mock.calls[0][0], "https://devfeed.test");
  expect(url.pathname).toBe("/api/v1/user/feed");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    limit: "24",
    cursor: "next+/=",
    sort: "most_liked",
    content_type: "news",
    source_id: sourceId,
  });
  const recovery = new URL(
    screen.getByRole("link", { name: "Show updated feed" }).getAttribute("href")!,
    "https://devfeed.test",
  );
  expect(recovery.pathname).toBe("/");
  expect(recovery.searchParams.get("sort")).toBe("most_liked");
  expect(recovery.searchParams.get("source_id")).toBe(sourceId);
  expect(recovery.searchParams.has("cursor")).toBe(false);
});

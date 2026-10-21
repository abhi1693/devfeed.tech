// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createColumnRequestQueue, columnFeedLimits } from "@/lib/column-feed";
import { useColumnFeed } from "@/lib/use-column-feed";
import type { ReaderColumnFeed } from "@/lib/reader-preferences";
import type { Article } from "@/lib/types";
import { article } from "./fixtures";

const session = vi.hoisted(() => ({
  user: { user_id: "reader-a", csrf_token: "csrf-a", expires_at: 4102444800 },
  loading: false,
  sessionRevision: 1,
}));
const preferences = vi.hoisted(() => ({
  content_types: ["news"],
  languages: ["en"],
  loading: false,
  unavailable: false,
  busy: false,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));

function item(index: number): Article {
  return {
    ...article,
    id: `aaaaaaaa-aaaa-4aaa-8aaa-${index.toString(16).padStart(12, "0")}`,
    slug: `column-article-${index}`,
    title: `Column article ${index}`,
    content_type: "news",
  };
}
function page(items: Article[], cursor: string | null = null) {
  return Response.json({
    items,
    next_cursor: cursor,
    status: "ready",
    has_interests: true,
    reasons: {},
  });
}
function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const latest: ReaderColumnFeed = { kind: "latest" };
const source: ReaderColumnFeed = { kind: "source", id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" };

beforeEach(() => {
  session.user = { user_id: "reader-a", csrf_token: "csrf-a", expires_at: 4102444800 };
  session.loading = false;
  session.sessionRevision = 1;
  preferences.content_types = ["news"];
  preferences.languages = ["en"];
  preferences.loading = false;
  preferences.unavailable = false;
  preferences.busy = false;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("isolates a failed feed from another column and retries only the failed column", async () => {
  const queue = createColumnRequestQueue();
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.href);
    return url.searchParams.has("source_id")
      ? Response.json({ detail: "Unavailable" }, { status: 503 })
      : page([item(1)]);
  });
  vi.stubGlobal("fetch", fetcher);
  const good = renderHook(() => useColumnFeed(latest, undefined, queue));
  const bad = renderHook(() => useColumnFeed(source, "rust-blog", queue));
  await waitFor(() => expect(good.result.current.articles).toHaveLength(1));
  await waitFor(() => expect(bad.result.current.error?.status).toBe(503));
  expect(good.result.current.error).toBeNull();
  const goodReads = fetcher.mock.calls.filter(
    ([input]) => !new URL(String(input), window.location.href).searchParams.has("source_id"),
  ).length;
  fetcher.mockImplementation(async () => page([item(2)]));
  act(() => bad.result.current.refresh());
  await waitFor(() => expect(bad.result.current.articles[0]?.id).toBe(item(2).id));
  expect(
    fetcher.mock.calls.filter(
      ([input]) => !new URL(String(input), window.location.href).searchParams.has("source_id"),
    ),
  ).toHaveLength(goodReads);
  expect(good.result.current.articles.map(({ id }) => id)).toEqual([item(1).id]);
});

it("paginates columns independently, deduplicates articles, and ends a repeated cursor", async () => {
  const queue = createColumnRequestQueue();
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.href);
    if (url.searchParams.has("source_id")) return page([item(3)], "source-next");
    return url.searchParams.has("cursor")
      ? page([item(1), item(2), item(2)], "latest-next")
      : page([item(1)], "latest-next");
  });
  vi.stubGlobal("fetch", fetcher);
  const first = renderHook(() => useColumnFeed(latest, undefined, queue));
  const second = renderHook(() => useColumnFeed(source, "rust-blog", queue));
  await waitFor(() => expect(first.result.current.cursor).toBe("latest-next"));
  await waitFor(() => expect(second.result.current.cursor).toBe("source-next"));
  await act(async () => {
    await first.result.current.loadMore();
  });
  expect(first.result.current.articles.map(({ id }) => id)).toEqual([item(1).id, item(2).id]);
  expect(first.result.current.cursor).toBeNull();
  expect(second.result.current.articles.map(({ id }) => id)).toEqual([item(3).id]);
  expect(second.result.current.cursor).toBe("source-next");
  expect(
    fetcher.mock.calls.filter(([input]) =>
      new URL(String(input), window.location.href).searchParams.has("source_id"),
    ),
  ).toHaveLength(1);
});

it("keeps a failed pagination cursor retryable and preserves loaded articles", async () => {
  const queue = createColumnRequestQueue();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(page([item(1)], "next+/="))
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockResolvedValueOnce(page([item(2)]));
  vi.stubGlobal("fetch", fetcher);
  const feed = renderHook(() => useColumnFeed(latest, undefined, queue));
  await waitFor(() => expect(feed.result.current.cursor).toBe("next+/="));
  await act(async () => {
    await feed.result.current.loadMore();
  });
  expect(feed.result.current.error?.status).toBe(503);
  expect(feed.result.current.articles.map(({ id }) => id)).toEqual([item(1).id]);
  expect(feed.result.current.cursor).toBe("next+/=");
  await act(async () => {
    await feed.result.current.loadMore();
  });
  expect(feed.result.current.error).toBeNull();
  expect(feed.result.current.articles.map(({ id }) => id)).toEqual([item(1).id, item(2).id]);
  expect(
    new URL(String(fetcher.mock.calls[2][0]), window.location.href).searchParams.get("cursor"),
  ).toBe("next+/=");
});

it("bounds retained pages and articles and only restarts when explicitly refreshed", async () => {
  const queue = createColumnRequestQueue();
  let generation = 0;
  const fetcher = vi.fn(async () => {
    const offset = generation++ * columnFeedLimits.pageSize;
    return page(
      Array.from({ length: columnFeedLimits.pageSize }, (_, index) => item(offset + index + 1)),
      `cursor-${generation}`,
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const feed = renderHook(() => useColumnFeed(latest, undefined, queue));
  await waitFor(() => expect(feed.result.current.articles).toHaveLength(columnFeedLimits.pageSize));
  for (let index = 1; index < columnFeedLimits.retainedPages; index++)
    await act(async () => {
      await feed.result.current.loadMore();
    });
  expect(feed.result.current.pages).toHaveLength(3);
  expect(feed.result.current.articles).toHaveLength(72);
  expect(feed.result.current.limited).toBe(true);
  await act(async () => {
    await feed.result.current.loadMore();
  });
  expect(fetcher).toHaveBeenCalledTimes(3);
  act(() => feed.result.current.refresh());
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  await waitFor(() => expect(feed.result.current.articles).toHaveLength(24));
  expect(feed.result.current.pages).toHaveLength(1);
  expect(feed.result.current.limited).toBe(false);
});

it("cancels an edited selection and ignores its late response", async () => {
  const pending = deferred();
  let oldSignal: AbortSignal | null | undefined;
  const fetcher = vi
    .fn()
    .mockImplementationOnce((_input, init?: RequestInit) => {
      oldSignal = init?.signal;
      return pending.promise;
    })
    .mockResolvedValue(page([item(2)]));
  vi.stubGlobal("fetch", fetcher);
  const queue = createColumnRequestQueue();
  const feed = renderHook(
    ({ selection }: { selection: ReaderColumnFeed }) =>
      useColumnFeed(selection, "rust-blog", queue),
    { initialProps: { selection: latest as ReaderColumnFeed } },
  );
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const obsoleteLoadMore = feed.result.current.loadMore;
  feed.rerender({ selection: source });
  expect(oldSignal?.aborted).toBe(true);
  await waitFor(() => expect(feed.result.current.articles[0]?.id).toBe(item(2).id));
  await act(async () => pending.resolve(page([item(1)])));
  expect(feed.result.current.articles.map(({ id }) => id)).toEqual([item(2).id]);
  const reads = fetcher.mock.calls.length;
  await act(async () => {
    await obsoleteLoadMore();
  });
  expect(fetcher).toHaveBeenCalledTimes(reads);
});

it("aborts a removed column without publishing its delayed data or scheduling pagination", async () => {
  const pending = deferred();
  let signal: AbortSignal | null | undefined;
  const fetcher = vi.fn((_input, init?: RequestInit) => {
    signal = init?.signal;
    return pending.promise;
  });
  vi.stubGlobal("fetch", fetcher);
  const queue = createColumnRequestQueue();
  const feed = renderHook(() => useColumnFeed(latest, undefined, queue));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const obsoleteLoadMore = feed.result.current.loadMore;
  feed.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => pending.resolve(page([item(1)], "next")));
  await act(async () => {
    await obsoleteLoadMore();
  });
  expect(fetcher).toHaveBeenCalledOnce();
});

it.each([
  ["latest", { kind: "latest" }, undefined, "/api/v1/feed", "diverse", "true"],
  ["recommended", { kind: "recommended" }, undefined, "/api/v1/user/feed", undefined, undefined],
  ["source", source, "rust-blog", "/api/v1/feed", "source_id", source.id],
  [
    "topic",
    { kind: "topic", id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" },
    "rust",
    "/api/v1/feed",
    "topic",
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  ],
  [
    "tag",
    { kind: "tag", id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc" },
    "c++",
    "/api/v1/feed",
    "tag",
    "c++",
  ],
] as const)(
  "keeps the %s column on its intended API and exact feed identity",
  async (_name, selection, slug, path, key, value) => {
    const fetcher = vi.fn().mockResolvedValue(page([item(1)]));
    vi.stubGlobal("fetch", fetcher);
    const queue = createColumnRequestQueue();
    const feed = renderHook(() => useColumnFeed(selection, slug, queue));
    await waitFor(() => expect(feed.result.current.articles).toHaveLength(1));
    const url = new URL(String(fetcher.mock.calls[0][0]), window.location.href);
    expect(url.pathname).toBe(path);
    expect(url.searchParams.get("limit")).toBe("24");
    if (key) expect(url.searchParams.get(key)).toBe(value);
    expect(url.searchParams.has("q")).toBe(false);
  },
);

it("rejects malformed feed receipts and bounds oversized pages before retaining articles", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ items: {}, next_cursor: null }))
    .mockResolvedValueOnce(page(Array.from({ length: 100 }, (_, index) => item(index))));
  vi.stubGlobal("fetch", fetcher);
  const queue = createColumnRequestQueue();
  const feed = renderHook(() => useColumnFeed(latest, undefined, queue));
  await waitFor(() => expect(feed.result.current.error?.status).toBe(502));
  expect(feed.result.current.articles).toHaveLength(0);
  act(() => feed.result.current.refresh());
  await waitFor(() => expect(feed.result.current.articles).toHaveLength(columnFeedLimits.pageSize));
  expect(feed.result.current.error).toBeNull();
  expect(feed.result.current.pages).toHaveLength(1);
});

it("loads a pagination cursor once when its control is activated repeatedly", async () => {
  const pending = deferred();
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(page([item(1)], "next"))
    .mockReturnValueOnce(pending.promise);
  vi.stubGlobal("fetch", fetcher);
  const queue = createColumnRequestQueue();
  const feed = renderHook(() => useColumnFeed(latest, undefined, queue));
  await waitFor(() => expect(feed.result.current.cursor).toBe("next"));
  let first!: Promise<string | undefined>;
  let repeated!: Promise<string | undefined>;
  act(() => {
    first = feed.result.current.loadMore();
    repeated = feed.result.current.loadMore();
  });
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  await act(async () => {
    pending.resolve(page([item(2)]));
    await Promise.all([first, repeated]);
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(feed.result.current.articles.map(({ id }) => id)).toEqual([item(1).id, item(2).id]);
});

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { InfiniteChoices } from "@/components/infinite-choices";
import { topic, source } from "./fixtures";

vi.mock("@/components/source-follow", () => ({
  SourceFollow: ({
    sourceId,
    returnTo,
    compact,
  }: {
    sourceId: string;
    returnTo: string;
    compact?: boolean;
  }) => (
    <button data-compact={compact} data-return-to={returnTo}>
      Follow {sourceId}
    </button>
  ),
}));
vi.mock("@/components/topic-follow", () => ({
  TopicFollow: ({ topicId, returnTo }: { topicId: string; returnTo: string }) => (
    <button data-return-to={returnTo}>Follow {topicId}</button>
  ),
}));
let intersect: () => void;
const fetcher = vi.fn();
beforeEach(() => {
  fetcher.mockReset();
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
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each(["topics", "sources"] as const)(
  "appends %s without duplicate cards, preserves routes, and stops at the end",
  async (kind) => {
    const item = kind === "topics" ? topic : source;
    const first = Array.from({ length: 60 }, (_, i) => ({
      ...item,
      id: String(i),
      name: `Item ${i}`,
    }));
    const extra = { ...item, id: "extra", name: "Next item" };
    let resolve!: (response: Response) => void;
    fetcher.mockReturnValue(
      new Promise<Response>((done) => {
        resolve = done;
      }),
    );
    render(
      <InfiniteCatalog kind={kind} initialPage={{ items: first, next_cursor: "server+cursor" }} />,
    );
    expect(screen.queryByRole("link", { name: `More ${kind}` })).toBeNull();
    expect(screen.queryByRole("button", { name: `More ${kind}` })).toBeNull();
    await act(async () => {
      intersect();
      intersect();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/${kind}?offset=server%2Bcursor`);
    await act(async () =>
      resolve(Response.json({ items: [first[0], extra, extra], next_cursor: null })),
    );
    expect(screen.getAllByRole("heading", { name: "Item 0" })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: "Next item" })).toHaveLength(1);
    expect(screen.getByText(`You’ve seen all ${kind}.`)).toBeDefined();
    expect(screen.queryByRole("link", { name: `More ${kind}` })).toBeNull();
    expect(screen.getByRole("link", { name: /^Next item/ }).getAttribute("href")).toBe(
      `/${kind}/${item.slug}`,
    );
    if (kind === "topics")
      expect(
        screen.getByRole("button", { name: "Follow extra" }).getAttribute("data-return-to"),
      ).toBe(`/topics/${topic.slug}`);
    else
      expect(
        screen.getByRole("button", { name: "Follow extra" }).getAttribute("data-return-to"),
      ).toBe(`/sources/${source.slug}`);
    if (kind === "sources")
      expect(
        screen.getByRole("button", { name: "Follow extra" }).getAttribute("data-compact"),
      ).toBe("true");
  },
);

it("retains topics on error, retries manually, and cancels pagination in a background tab", async () => {
  const items = Array.from({ length: 60 }, (_, i) => ({
    ...topic,
    id: String(i),
    name: `Topic ${i}`,
  }));
  fetcher
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockImplementationOnce(() => new Promise(() => {}))
    .mockResolvedValue(Response.json({ items: [], next_cursor: null }));
  render(<InfiniteCatalog kind="topics" initialPage={{ items, next_cursor: "60" }} />);
  await act(async () => intersect());
  expect(screen.getByText("Couldn’t load more topics.")).toBeDefined();
  expect(screen.getByRole("heading", { name: "Topic 0" })).toBeDefined();
  await act(async () => fireEvent.click(screen.getByRole("link", { name: "Try again" })));
  const signal = fetcher.mock.calls[1][1].signal;
  vi.mocked(document.hasFocus).mockReturnValue(false);
  await act(async () => window.dispatchEvent(new Event("blur")));
  expect(signal.aborted).toBe(true);
  await act(async () => intersect());
  expect(fetcher).toHaveBeenCalledTimes(2);
  vi.mocked(document.hasFocus).mockReturnValue(true);
  await act(async () => window.dispatchEvent(new Event("focus")));
  await act(async () => intersect());
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(screen.getByText("You’ve seen all topics.")).toBeDefined();
});

it("fetches one preference page, waits for scrolling, and searches beyond loaded items", async () => {
  const items = Array.from({ length: 60 }, (_, i) => ({ id: String(i), name: `Topic ${i}` }));
  fetcher
    .mockResolvedValueOnce(Response.json({ items, next_cursor: "60" }))
    .mockResolvedValueOnce(
      Response.json({ items: [{ id: "119", name: "Topic 119" }], next_cursor: null }),
    )
    .mockResolvedValueOnce(
      Response.json({ items: [{ id: "999", name: "Remote topic" }], next_cursor: null }),
    );
  const choices = (visible: typeof items) => (
    <div>
      {visible.map((item) => (
        <button key={item.id}>{item.name}</button>
      ))}
    </div>
  );
  const view = render(<InfiniteChoices label="topics">{choices}</InfiniteChoices>);
  await screen.findByRole("button", { name: "Topic 0" });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Topic 119" })).toBeNull();
  await act(async () => intersect());
  expect(await screen.findByRole("button", { name: "Topic 119" })).toBeDefined();
  view.rerender(
    <InfiniteChoices label="topics" query="Remote topic">
      {choices}
    </InfiniteChoices>,
  );
  await screen.findByRole("button", { name: "Remote topic" });
  expect(new URL(fetcher.mock.calls[2][0], "https://test").searchParams.get("q")).toBe(
    "Remote topic",
  );
  expect(screen.queryByRole("button", { name: "Topic 0" })).toBeNull();
});

it("cancels a stale catalog search and retries a failed replacement without old results", async () => {
  let staleSignal: AbortSignal;
  fetcher
    .mockImplementationOnce((_url, options) => {
      staleSignal = options.signal;
      return new Promise(() => {});
    })
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockResolvedValueOnce(
      Response.json({ items: [{ id: "new", name: "New result" }], next_cursor: null }),
    );
  const choices = (items: { id: string; name: string }[]) => (
    <div>
      {items.map((item) => (
        <button key={item.id}>{item.name}</button>
      ))}
    </div>
  );
  const view = render(<InfiniteChoices label="sources">{choices}</InfiniteChoices>);
  view.rerender(
    <InfiniteChoices label="sources" query="new">
      {choices}
    </InfiniteChoices>,
  );
  await screen.findByText("Couldn’t load sources.");
  expect(staleSignal!.aborted).toBe(true);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
  expect(await screen.findByRole("button", { name: "New result" })).toBeDefined();
  expect(fetcher).toHaveBeenCalledTimes(3);
});

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InfiniteCatalog } from "@/components/infinite-catalog";
import { InfiniteChoices } from "@/components/infinite-choices";
import { topic, source } from "./fixtures";

vi.mock("@/components/source-follow", () => ({
  SourceFollow: ({ sourceId }: { sourceId: string }) => <button>Follow {sourceId}</button>,
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
    render(<InfiniteCatalog kind={kind} initialItems={first} offset={60} />);
    expect(screen.getByRole("link", { name: `More ${kind}` }).getAttribute("href")).toBe(
      `/${kind}?offset=120`,
    );
    await act(async () => {
      intersect();
      intersect();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/${kind}?offset=120`);
    await act(async () =>
      resolve(Response.json({ items: [first[0], extra, extra], next_cursor: null })),
    );
    expect(screen.getAllByRole("heading", { name: "Item 0" })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: "Next item" })).toHaveLength(1);
    expect(screen.getByText(`You’ve seen all ${kind}.`)).toBeDefined();
    expect(screen.queryByRole("link", { name: `More ${kind}` })).toBeNull();
    if (kind === "sources")
      expect(screen.getByRole("button", { name: "Follow extra" })).toBeDefined();
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
  render(<InfiniteCatalog kind="topics" initialItems={items} offset={0} />);
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

it("reveals preference choices incrementally and resets the visible batch when search changes", async () => {
  const items = Array.from({ length: 130 }, (_, i) => `Topic ${i}`);
  const choices = (visible: string[]) => (
    <div>
      {visible.map((item) => (
        <button key={item}>{item}</button>
      ))}
    </div>
  );
  const view = render(
    <InfiniteChoices key="all" items={items} label="topics">
      {choices}
    </InfiniteChoices>,
  );
  expect(screen.queryByRole("button", { name: "Topic 60" })).toBeNull();
  await act(async () => intersect());
  expect(screen.getByRole("button", { name: "Topic 119" })).toBeDefined();
  view.rerender(
    <InfiniteChoices key="129" items={[items[129]]} label="topics">
      {choices}
    </InfiniteChoices>,
  );
  expect(screen.getByRole("button", { name: "Topic 129" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "More topics" })).toBeNull();
});

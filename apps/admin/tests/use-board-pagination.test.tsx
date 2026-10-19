// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useBoardPagination } from "@/lib/use-board-pagination";
import { notifyFailure } from "@/lib/notifications";
import { boardItems, compareBoardItems } from "@/lib/board-items";

vi.mock("@/lib/notifications", () => ({ notifyFailure: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
type Stage = "todo" | "done";
type Page = { stage: Stage; items: string[]; next_cursor: number | null };
const first: Page = { stage: "todo", items: ["one"], next_cursor: 0 };
function deferred() {
  let resolve!: (value: Page) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<Page>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function setup(load: (column: Page, signal: AbortSignal) => Promise<Page>) {
  return renderHook(
    ({ snapshot }) =>
      useBoardPagination<object, Stage, Page>({
        snapshot,
        load,
        merge: (previous, next) => ({ ...next, items: [...previous.items, ...next.items] }),
        failureMessage: "Could not load reviews",
      }),
    { initialProps: { snapshot: {} } },
  );
}

it("accepts a zero cursor, fences same-render double clicks and appends a page", async () => {
  const response = deferred();
  const load = vi.fn(() => response.promise);
  const { result } = setup(load);
  let pending!: Promise<void>;
  act(() => {
    pending = result.current.more(first);
    void result.current.more(first);
  });
  expect(load).toHaveBeenCalledTimes(1);
  expect(result.current.loading).toBe("todo");
  await act(async () => {
    response.resolve({ stage: "todo", items: ["two"], next_cursor: null });
    await pending;
  });
  expect(result.current.columns.todo?.items).toEqual(["one", "two"]);
  expect(result.current.loading).toBeUndefined();
  await act(async () => {
    await result.current.more(result.current.columns.todo!);
  });
  expect(load).toHaveBeenCalledTimes(1);
});

it.each(["success", "failure"])(
  "ignores stale %s after refresh while allowing a fresh request",
  async (outcome) => {
    const old = deferred();
    const fresh = deferred();
    const load = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const { result, rerender } = setup(load);
    let oldRequest!: Promise<void>;
    act(() => {
      oldRequest = result.current.more(first);
    });
    const signal: AbortSignal = load.mock.calls[0][1];
    rerender({ snapshot: {} });
    expect(signal.aborted).toBe(true);
    expect(result.current.loading).toBeUndefined();
    let newRequest!: Promise<void>;
    act(() => {
      newRequest = result.current.more(first);
    });
    await act(async () => {
      if (outcome === "success")
        old.resolve({ stage: "todo", items: ["stale"], next_cursor: null });
      else old.reject(new Error("outdated failure"));
      await oldRequest;
    });
    expect(result.current.loading).toBe("todo");
    expect(result.current.columns).toEqual({});
    expect(result.current.error).toBeUndefined();
    expect(notifyFailure).not.toHaveBeenCalled();
    await act(async () => {
      fresh.resolve({ stage: "todo", items: ["current"], next_cursor: null });
      await newRequest;
    });
    expect(result.current.columns.todo?.items).toEqual(["one", "current"]);
  },
);

it("shows a current lane error, supports retry and clears the error on refresh", async () => {
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ stage: "todo", items: ["two"], next_cursor: null });
  const { result, rerender } = setup(load);
  await act(async () => {
    await result.current.more(first);
  });
  expect(result.current.error).toBe("todo");
  expect(notifyFailure).toHaveBeenCalledWith(expect.any(Error), "Could not load reviews");
  await act(async () => {
    await result.current.more(first);
  });
  expect(result.current.error).toBeUndefined();
  expect(result.current.columns.todo?.items).toEqual(["one", "two"]);
  rerender({ snapshot: {} });
  expect(result.current.columns).toEqual({});
  expect(result.current.error).toBeUndefined();
});

it("aborts pagination when its board unmounts", async () => {
  const response = deferred();
  const load = vi
    .fn<(column: Page, signal: AbortSignal) => Promise<Page>>()
    .mockReturnValue(response.promise);
  const { result, unmount } = setup(load);
  let pending!: Promise<void>;
  act(() => {
    pending = result.current.more(first);
  });
  unmount();
  expect(load.mock.calls[0][1].aborted).toBe(true);
  await act(async () => {
    response.reject(new Error("aborted"));
    await pending;
  });
  expect(notifyFailure).not.toHaveBeenCalled();
});

it("replaces moved items with the newer lane page and retains submillisecond order", () => {
  const old = { id: "one", stage: "todo" };
  const moved = { id: "one", stage: "done" };
  expect(
    boardItems([{ items: [old] }], [{ items: [moved] }, undefined], (item) => item.id),
  ).toEqual([moved]);
  const entry = (state: string, timestamp: string, key = "one") => ({ state, timestamp, key });
  const early = entry("waiting", "2026-10-19T08:00:00.000001Z");
  const late = entry("waiting", "2026-10-19T08:00:00.000002Z");
  expect(compareBoardItems(early, late)).toBeLessThan(0);
  expect(compareBoardItems({ ...late, state: "running" }, early)).toBeLessThan(0);
  expect(compareBoardItems(early, { ...early, key: "two" })).toBeLessThan(0);
  expect(compareBoardItems(early, early)).toBe(0);
});

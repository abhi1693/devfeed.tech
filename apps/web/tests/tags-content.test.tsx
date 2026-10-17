// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TagsContent, tagsHref } from "@/components/tags-content";

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const tags = [
  { id: "rust", name: "Rust", slug: "rust" },
  { id: "cpp", name: "C++", slug: "c++" },
  { id: "net", name: ".NET", slug: ".net" },
  { id: "db", name: "数据库", slug: "数据库" },
];
let intersect: () => void;
const fetcher = vi.fn();
beforeEach(() => {
  router.push.mockClear();
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

it("groups names alphabetically and keeps punctuation and Unicode in tag destinations", () => {
  render(<TagsContent tags={tags} query="C++" letter="C" nextCursor={null} />);
  expect(screen.getAllByRole("region").map((node) => node.getAttribute("aria-label"))).toEqual([
    "Tags: C",
    "Tags: R",
    "Tags: #",
  ]);
  for (const tag of tags)
    expect(screen.getByRole("link", { name: tag.name }).getAttribute("href")).toBe(
      `/tags/${encodeURIComponent(tag.slug)}`,
    );
  const alphabet = within(screen.getByRole("navigation", { name: "Filter tags by letter" }));
  expect(alphabet.getByRole("link", { name: "C" }).getAttribute("aria-current")).toBe("page");
  expect(alphabet.getByRole("link", { name: "#" }).getAttribute("href")).toBe(
    "/tags?q=C%2B%2B&letter=%23",
  );
  expect(alphabet.getByRole("link", { name: "All" }).getAttribute("href")).toBe("/tags?q=C%2B%2B");
  expect(screen.getByText("You’ve seen all matching tags.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /^(Next|Previous) tags$/ })).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it("submits trimmed bounded searches while preserving the selected letter", () => {
  render(<TagsContent tags={tags} query="previous" letter="C" nextCursor={null} />);
  const input = screen.getByRole("textbox", { name: "Search all tags" });
  expect(input).toHaveProperty("value", "previous");
  fireEvent.change(input, { target: { value: `  ${"x".repeat(250)}  ` } });
  fireEvent.submit(screen.getByRole("search"));
  expect(router.push).toHaveBeenLastCalledWith(`/tags?q=${"x".repeat(200)}&letter=C`);
  fireEvent.change(input, { target: { value: "   " } });
  fireEvent.submit(screen.getByRole("search"));
  expect(router.push).toHaveBeenLastCalledWith("/tags?letter=C");
  expect(tagsHref("", "")).toBe("/tags");
  expect(tagsHref("C++", "C", 60)).toBe("/tags?q=C%2B%2B&letter=C&offset=60");
});

it("retains tags after a page failure and retries without duplicate entries or requests", async () => {
  let finish!: (response: Response) => void;
  fetcher.mockResolvedValueOnce(Response.json({}, { status: 503 })).mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  render(<TagsContent tags={[tags[1]]} query="C++" letter="C" nextCursor="60" />);
  await act(async () => intersect());
  expect(screen.getByText("Couldn’t load more tags.")).toBeTruthy();
  expect(screen.getByRole("link", { name: "C++" })).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Try again" })));
  expect(screen.getByText("Loading more tags…")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/tags?offset=60&q=C%2B%2B&letter=C");
  expect(fetcher.mock.calls[1][1]).toMatchObject({
    cache: "no-store",
    signal: expect.any(AbortSignal),
  });
  await act(async () =>
    finish(Response.json({ items: [tags[1], tags[0], tags[0]], next_cursor: null })),
  );
  expect(screen.getAllByRole("link", { name: "C++" })).toHaveLength(1);
  expect(screen.getAllByRole("link", { name: "Rust" })).toHaveLength(1);
  expect(screen.getByText("You’ve seen all matching tags.")).toBeTruthy();
});

it("offers an unfiltered directory when no tags match", () => {
  render(<TagsContent tags={[]} nextCursor={null} />);
  expect(screen.getByRole("heading", { name: "No tags found" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Browse all tags" }).getAttribute("href")).toBe("/tags");
  expect(screen.queryByRole("region")).toBeNull();
  expect(screen.queryByText("You’ve seen all matching tags.")).toBeNull();
});

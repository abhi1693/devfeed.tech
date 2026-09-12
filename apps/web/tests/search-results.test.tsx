// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchFailure, SearchResults } from "@/components/search-results";
import { searchKinds, type SearchHit, type SearchResponse } from "@/lib/search";

const { refresh } = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
const fetcher = vi.fn();
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
  refresh.mockReset();
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows articles first and every catalogue section without automatically fetching pages", () => {
  render(<SearchResults result={result()} />);
  expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
    "Articles",
    "Topics",
    "Sources",
    "Tags",
  ]);
  expect(screen.getByRole("link", { name: /tags/ }).getAttribute("href")).toBe("/tags/kubernetes");
  expect(fetcher).not.toHaveBeenCalled();
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
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "More topics" })));
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/search?q=kubernetes&section=topics&page=2");
  expect(screen.getAllByRole("link", { name: "topics" })).toHaveLength(1);
  expect(screen.getByRole("link", { name: "another-topic" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "More topics" })).toBeNull();
  expect(screen.getByRole("button", { name: "More articles" })).toBeDefined();
});

it("retains results on failure and cancels a retry when the tab loses focus", async () => {
  fetcher
    .mockResolvedValueOnce(Response.json({}, { status: 503 }))
    .mockImplementationOnce(() => new Promise(() => {}));
  render(<SearchResults result={result()} />);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "More articles" })));
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
  expect(screen.getByRole("button", { name: "More articles" })).toBeDefined();
  expect(screen.queryByText(/No results for/)).toBeNull();
});

it("retries the server search after an outage", () => {
  render(<SearchFailure />);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(refresh).toHaveBeenCalledOnce();
});

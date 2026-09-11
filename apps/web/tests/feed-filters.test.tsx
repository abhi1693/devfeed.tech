// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FeedFiltersBar } from "@/components/feed-filters";
import { parseFilters } from "@/lib/feed-query";
import { source } from "./fixtures";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("applies filters to a content route without losing the topic or search", () => {
  render(<FeedFiltersBar filters={parseFilters({ topic: "python", content_type: "tutorial", q: "guide", cursor: "old" })} sources={[source]} />);
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: "en" } });
  fireEvent.change(screen.getByLabelText("Source"), { target: { value: source.id } });
  fireEvent.submit(screen.getByLabelText("Language").closest("form")!);
  const url = new URL(router.push.mock.calls[0][0], "http://localhost");
  expect(url.pathname).toBe("/topics/python/tutorials");
  expect(Object.fromEntries(url.searchParams)).toEqual({ q: "guide", language: "en", source_id: source.id });
});

it("removes the source from the path when selecting all sources", () => {
  render(<FeedFiltersBar filters={parseFilters({ source_id: source.id, content_type: "news" })} sources={[source]} />);
  fireEvent.change(screen.getByLabelText("Source"), { target: { value: "" } });
  fireEvent.submit(screen.getByLabelText("Source").closest("form")!);
  expect(router.push).toHaveBeenCalledWith("/news");
});

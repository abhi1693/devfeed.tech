// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SearchFilters } from "@/components/search-filters";
import { parseSearchOptions } from "@/lib/search";
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
afterEach(() => {
  cleanup();
  push.mockReset();
});
it("applies a shareable query, date range and order", () => {
  render(
    <SearchFilters query="cloud & data" options={parseSearchOptions(new URLSearchParams())} />,
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Results" }));
  fireEvent.click(screen.getByRole("option", { name: "Articles" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Article order" }));
  fireEvent.click(screen.getByRole("option", { name: "Newest first" }));
  fireEvent.click(screen.getByRole("button", { name: "Article date from" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Year" }));
  fireEvent.click(screen.getByRole("option", { name: "2026" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Month" }));
  fireEvent.click(screen.getByRole("option", { name: "September" }));
  fireEvent.click(screen.getByRole("button", { name: "Tuesday, September 1, 2026" }));
  fireEvent.submit(screen.getByRole("form", { name: "Search filters" }));
  const url = new URL(push.mock.calls[0][0], "http://localhost");
  expect(Object.fromEntries(url.searchParams)).toEqual({
    q: "cloud & data",
    section: "articles",
    sort: "newest",
    date_from: "2026-09-01",
  });
});
it("disables article-only controls for catalogue results and clears filters without losing the query", () => {
  render(
    <SearchFilters
      query="cloud"
      options={parseSearchOptions(new URLSearchParams("section=topics&sort=newest"))}
    />,
  );
  expect((screen.getByLabelText("Article order") as HTMLSelectElement).disabled).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Search filters" }));
  expect(push.mock.calls[0][0]).toBe("/search?q=cloud&section=topics");
  expect(screen.getByRole("link", { name: "Clear filters" }).getAttribute("href")).toBe(
    "/search?q=cloud",
  );
});
it("rejects malformed options rather than forwarding search-engine expressions", () => {
  expect(
    parseSearchOptions(new URLSearchParams("section=private&sort=bad&date_from=2026-02-30")),
  ).toEqual({ section: "", sort: "relevance", date_from: "", date_to: "" });
});

it("limits both date pickers to today and disallows selecting tomorrow", () => {
  const today = new Date().toISOString().slice(0, 10);
  const tomorrow = new Date(`${today}T12:00:00Z`);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  render(<SearchFilters query="cloud" options={parseSearchOptions(new URLSearchParams())} />);
  for (const field of ["date_from", "date_to"])
    expect(document.querySelector(`input[name="${field}"]`)?.getAttribute("max")).toBe(today);
  fireEvent.click(screen.getByRole("button", { name: "Article date from" }));
  const next = screen.getByRole("button", {
    name: new Intl.DateTimeFormat("en", { dateStyle: "full", timeZone: "UTC" }).format(tomorrow),
  });
  expect((next as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Next month" }) as HTMLButtonElement).disabled).toBe(
    true,
  );
});

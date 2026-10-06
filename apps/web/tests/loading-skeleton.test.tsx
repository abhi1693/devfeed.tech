// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LoadingSkeleton } from "@/components/loading-skeleton";

const preferences = vi.hoisted(() => ({
  view: "compact" as "compact" | "cards",
  loading: false,
}));
vi.mock("@/components/feed-preferences", () => ({ useFeedPreferences: () => preferences }));

beforeEach(() => {
  preferences.view = "compact";
  preferences.loading = false;
});
afterEach(cleanup);

it("waits for saved layout before choosing a feed shimmer instead of flashing the default grid", () => {
  preferences.loading = true;
  preferences.view = "cards";
  const view = render(<LoadingSkeleton />);
  expect(screen.getByRole("status", { name: "Loading articles…" })).toBeTruthy();
  expect(view.container.querySelector(".shimmer")).toBeNull();
  preferences.loading = false;
  preferences.view = "compact";
  view.rerender(<LoadingSkeleton />);
  expect(view.container.querySelectorAll(".loading-skeleton")).toHaveLength(1);
  expect(view.container.querySelectorAll(".skeleton-list-row")).toHaveLength(6);
  expect(view.container.querySelector(".skeleton-image")).toBeNull();
});

it("announces compact loading once while showing aligned single-line placeholders without controls", () => {
  const { container } = render(<LoadingSkeleton label="Loading your feed…" />);
  expect(screen.getByRole("status", { name: "Loading your feed…" })).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
  const placeholder = container.querySelector('.loading-skeleton[aria-hidden="true"]')!;
  expect(placeholder.querySelector(".article-table")).toBeTruthy();
  const rows = placeholder.querySelectorAll("tbody tr");
  expect(rows).toHaveLength(6);
  for (const row of rows) {
    expect(row.children).toHaveLength(4);
    expect(row.children[0].querySelectorAll(".skeleton-line")).toHaveLength(1);
    expect(row.querySelector(".source-column .skeleton-line")).toBeTruthy();
    expect(row.querySelector(".date-column .skeleton-line")).toBeTruthy();
    expect(row.querySelector(".bookmark-column .skeleton-bookmark")).toBeTruthy();
  }
  expect(placeholder.querySelector(".skeleton-image")).toBeNull();
});

it("keeps card placeholders in grid view", () => {
  preferences.view = "cards";
  const { container } = render(<LoadingSkeleton />);
  expect(container.querySelector(".article-table")).toBeNull();
  expect(container.querySelectorAll(".skeleton-image")).toHaveLength(6);
});

it.each(["form", "topics", "sources"] as const)(
  "preserves %s placeholders when the feed uses compact view",
  (kind) => {
    const { container } = render(<LoadingSkeleton kind={kind} />);
    expect(container.querySelector(".article-table")).toBeNull();
    expect(container.querySelectorAll(".skeleton-item")).toHaveLength(kind === "form" ? 3 : 6);
    if (kind !== "form") expect(container.querySelectorAll(".skeleton-topic-icon")).toHaveLength(6);
  },
);

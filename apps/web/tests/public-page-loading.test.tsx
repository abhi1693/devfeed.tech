// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PublicPageLoading } from "@/components/public-page-loading";

vi.mock("@/components/feed-preferences", () => ({
  useFeedPreferences: () => ({ view: "cards", loading: false }),
}));
afterEach(cleanup);
it.each([
  ["Loading articles…", "feed"],
  ["Loading sources…", "sources"],
  ["Loading topics…", "topics"],
] as const)("provides an accessible %s state", (label, kind) => {
  const { container } = render(<PublicPageLoading kind={kind} />);
  expect(screen.getByRole("status", { name: label })).toBeTruthy();
  expect(container.querySelector(`.loading-skeleton.${kind}`)?.getAttribute("aria-hidden")).toBe(
    "true",
  );
  expect(screen.queryByRole("main")).toBeNull();
});

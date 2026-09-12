// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ArticleImage } from "@/components/article-image";
import { CatalogIcon } from "@/components/catalog-icon";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("replaces article images that failed before hydration", () => {
  vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
  vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(0);
  const view = render(<ArticleImage src="https://example.test/missing.png" label="Docker" />);
  expect(view.container.querySelector("img")).toBeNull();
  expect(view.getByText("Docker")).toBeTruthy();
});
it("replaces catalog logos that failed before hydration", () => {
  vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
  vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(0);
  const view = render(<CatalogIcon url="https://example.test/missing.png" source />);
  expect(view.container.querySelector("img")).toBeNull();
  expect(view.container.querySelector(".lucide-rss")).toBeTruthy();
});
it("keeps loaded images and falls back on later failures", () => {
  vi.spyOn(HTMLImageElement.prototype, "complete", "get").mockReturnValue(true);
  vi.spyOn(HTMLImageElement.prototype, "naturalWidth", "get").mockReturnValue(640);
  const view = render(<ArticleImage src="https://example.test/cover.png" label="Docker" />);
  expect(view.container.querySelector("img")).toBeTruthy();
  fireEvent.error(view.container.querySelector("img")!);
  expect(view.getByText("Docker")).toBeTruthy();
  view.rerender(<ArticleImage src="https://example.test/new-cover.png" label="Docker" />);
  expect(view.container.querySelector("img")?.getAttribute("src")).toBe(
    "https://example.test/new-cover.png",
  );
});

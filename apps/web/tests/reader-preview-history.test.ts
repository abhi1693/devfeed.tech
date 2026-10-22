import { describe, expect, it } from "vitest";
import { createPreviewHistory } from "@/lib/reader-preview-history";

const url = (path: string) => new URL(path, "https://devfeed.tech");

describe("retained article preview history", () => {
  it.each(["/latest", "/?content_type=news", "/read-later", "/topics/rust?tag=backend#articles"])(
    "recognizes a preview returning to its exact background %s",
    (background) => {
      const history = createPreviewHistory();
      history.navigate(url(background), "/articles/rust-traits");
      history.commit(url("/articles/rust-traits"));
      expect(history.restore(url(background))).toBe(true);
    },
  );

  it("retains the known background across preview replacements and repeated Back/Forward", () => {
    const history = createPreviewHistory();
    const background = url("/latest?tag=rust#articles");
    history.navigate(background, "/articles/first");
    history.commit(url("/articles/first"));
    history.navigate(url("/articles/first"), "/articles/second");
    history.commit(url("/articles/second"));
    expect(history.restore(background)).toBe(true);
    expect(history.restore(url("/articles/second"))).toBe(false);
    history.commit(url("/articles/second"));
    expect(history.restore(background)).toBe(true);
  });

  it.each([
    "/latest?tag=other#articles",
    "/latest?tag=rust#other",
    "/latest?tag=rust",
    "/read-later?tag=rust#articles",
    "https://elsewhere.test/latest?tag=rust#articles",
  ])("does not trust a return to a different URL: %s", (destination) => {
    const history = createPreviewHistory();
    const background = url("/latest?tag=rust#articles");
    history.navigate(background, "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    expect(history.restore(url(destination))).toBe(false);
    expect(history.restore(background)).toBe(false);
  });

  it("does not infer retained content from a direct article entry", () => {
    const history = createPreviewHistory();
    expect(history.restore(url("/latest"))).toBe(false);
    history.navigate(url("/articles/first"), "/articles/second");
    history.commit(url("/articles/second"));
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it.each([
    "/read-later",
    "/missing",
    "/articles/%2Funsafe",
    "https://elsewhere.test/articles/rust-traits",
    "javascript:alert(1)",
  ])("clears preview proof after unrelated or unsupported navigation to %s", (destination) => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    history.navigate(url("/articles/rust-traits"), destination);
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it("cannot retain a background that the reader did not resolve", () => {
    const history = createPreviewHistory();
    history.navigate(url("/unknown"), "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    expect(history.restore(url("/unknown"))).toBe(false);
  });

  it("clears retained proof when the document or session is reset", () => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    history.reset();
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it("does not trust navigation intent when no intercepted preview committed", () => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/rust-traits");
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it.each(["/articles/another-article", "/articles/rust-traits#other", "/latest"])(
    "does not trust a preview committed at a different destination: %s",
    (committed) => {
      const history = createPreviewHistory();
      history.navigate(url("/latest"), "/articles/rust-traits");
      history.commit(url(committed));
      expect(history.restore(url("/latest"))).toBe(false);
    },
  );

  it("requires a replacement preview to commit before preserving its background", () => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/first");
    history.commit(url("/articles/first"));
    history.navigate(url("/articles/first"), "/articles/second");
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it("invalidates existing proof when another preview commits at a mismatched URL", () => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    history.commit(url("/articles/another-article"));
    expect(history.restore(url("/latest"))).toBe(false);
  });

  it("requires a forwarded preview to commit again before preserving its background", () => {
    const history = createPreviewHistory();
    history.navigate(url("/latest"), "/articles/rust-traits");
    history.commit(url("/articles/rust-traits"));
    expect(history.restore(url("/latest"))).toBe(true);
    expect(history.restore(url("/articles/rust-traits"))).toBe(false);
    expect(history.restore(url("/latest"))).toBe(false);
  });
});

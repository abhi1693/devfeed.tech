// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { ArticleShare, articleShareLinks } from "@/components/article-share";
import { ArticleCard } from "@/components/article-card";
import { ArticleTable } from "@/components/article-table";
import { article } from "./fixtures";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "clipboard");
});
const title = "Tailwind & Shopify: what's next? 日本語";
function open() {
  fireEvent.click(screen.getByRole("button", { name: `Share article: ${title}` }));
}
it("encodes the article URL and title using the requested provider formats", () => {
  const url = "https://devfeed.tech/articles/tailwind-shopify";
  const options = articleShareLinks(url, title);
  expect(options.map((o) => new URL(o.href).origin + new URL(o.href).pathname)).toEqual([
    "https://www.reddit.com/submit",
    "https://x.com/share",
    "https://www.linkedin.com/shareArticle",
  ]);
  for (const o of options) {
    const params = new URL(o.href).searchParams;
    expect(params.get("url")).toBe(url);
    expect(params.get(o.name === "X" ? "text" : "title")).toBe(title);
  }
});
it("offers exactly four options and copies the article route rather than the feed URL", async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
  render(<ArticleShare slug="tailwind-shopify" title={title} />);
  open();
  const panel = screen.getByRole("dialog", { name: "Share article" });
  expect(within(panel).getAllByRole("link")).toHaveLength(3);
  expect(within(panel).getAllByRole("button")).toHaveLength(1);
  for (const link of within(panel).getAllByRole("link")) {
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  }
  fireEvent.click(within(panel).getByRole("button", { name: "Copy link" }));
  await screen.findByText("Link copied.");
  expect(copy).toHaveBeenCalledWith(`${window.location.origin}/articles/tailwind-shopify`);
});
it.each([false, true])(
  "provides a selected manual-copy field when clipboard is unavailable or denied (%s)",
  async (denied) => {
    if (denied)
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: vi.fn().mockRejectedValue(new Error("Denied")) },
      });
    render(<ArticleShare slug="tailwind-shopify" title={title} />);
    open();
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
    const input = (await screen.findByRole("textbox", {
      name: "Article link",
    })) as HTMLInputElement;
    expect(input.value).toBe(`${window.location.origin}/articles/tailwind-shopify`);
    expect(document.activeElement).toBe(input);
    expect(input.selectionEnd).toBe(input.value.length);
    expect(screen.queryByText("Link copied.")).toBeNull();
  },
);
it("keeps the popup inside a native preview and isolates article arrow shortcuts", async () => {
  const arrows = vi.fn();
  render(
    <dialog open aria-label="Article preview" onKeyDown={arrows}>
      <ArticleShare slug="tailwind-shopify" title={title} label />
    </dialog>,
  );
  const trigger = screen.getByRole("button", { name: `Share article: ${title}` });
  open();
  const panel = screen.getByRole("dialog", { name: "Share article" });
  expect(panel.closest("dialog")).toBe(screen.getByRole("dialog", { name: "Article preview" }));
  fireEvent.keyDown(within(panel).getByRole("button"), { key: "ArrowRight" });
  expect(arrows).not.toHaveBeenCalled();
  fireEvent.keyDown(panel, { key: "Escape" });
  await act(async () => {});
  expect(screen.queryByRole("dialog", { name: "Share article" })).toBeNull();
  expect(screen.getByRole("dialog", { name: "Article preview" })).toBeTruthy();
  await waitFor(() => expect(document.activeElement).toBe(trigger));
});
it("ignores a clipboard result after the menu closes and reopens", async () => {
  let finish!: () => void;
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    },
  });
  render(<ArticleShare slug="tailwind-shopify" title={title} />);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  open();
  open();
  await act(async () => finish());
  expect(screen.queryByText("Link copied.")).toBeNull();
});
it("includes sharing in both card and compact feed layouts", () => {
  const view = render(<ArticleCard article={article} />);
  expect(screen.getByRole("button", { name: `Share article: ${article.title}` })).toBeTruthy();
  view.unmount();
  render(<ArticleTable articles={[article]} recommendations={{}} showHeader />);
  expect(screen.getByRole("button", { name: `Share article: ${article.title}` })).toBeTruthy();
});

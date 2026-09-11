// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { ArticleNavigationProvider, useArticleNavigation, type ArticleSequence } from "@/components/article-navigation";
import { ArticleModal } from "@/components/article-modal";
const { back, replace } = vi.hoisted(() => ({
  back: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ back, replace }), usePathname: () => "/articles/first" }));
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  back.mockClear();
  replace.mockClear();
});

it("opens a native modal and restores scrolling and focus on dismissal", () => {
  const show = vi
    .spyOn(HTMLDialogElement.prototype, "showModal")
    .mockImplementation(function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    });
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (
    this: HTMLDialogElement,
  ) {
    this.removeAttribute("open");
  });
  const trigger = document.createElement("button");
  document.body.append(trigger);
  trigger.focus();
  const view = render(
    <ArticleModal>
      <h1>Article</h1>
    </ArticleModal>,
  );
  expect(show).toHaveBeenCalledOnce();
  expect(document.body.style.overflow).toBe("hidden");
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).toHaveBeenCalledOnce();
  view.unmount();
  expect(document.body.style.overflow).toBe("");
  expect(document.activeElement).toBe(trigger);
  trigger.remove();
});
it("handles Escape through browser history", () => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(
    function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  );
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(() => {});
  render(<ArticleModal>Preview</ArticleModal>);
  fireEvent(
    screen.getByRole("dialog"),
    new Event("cancel", { bubbles: true, cancelable: true }),
  );
  expect(back).toHaveBeenCalledOnce();
});

it("closes direct article links onto the feed instead of leaving the site", () => {
  render(<ArticleModal direct>Preview</ArticleModal>);
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(replace).toHaveBeenCalledWith("/");
  expect(back).not.toHaveBeenCalled();
});

function Sequence({ value, children }: { value: ArticleSequence; children: React.ReactNode }) {
  const { setSequence } = useArticleNavigation();
  useEffect(() => { setSequence(value); }, [value, setSequence]);
  return children;
}
function preview(slugs: string[], loadMore: ArticleSequence["loadMore"] = async () => undefined, hasMore = false) {
  return render(<ArticleNavigationProvider><Sequence value={{ slugs, hasMore, loading: false, loadMore }}><ArticleModal><h1>First</h1><input aria-label="Search topics" /></ArticleModal></Sequence></ArticleNavigationProvider>);
}
it("navigates in feed order with buttons and unmodified arrow keys without adding history entries", () => {
  preview(["before", "first", "after"]);
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(replace).toHaveBeenLastCalledWith("/articles/after", { scroll: false });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowLeft" });
  expect(replace).toHaveBeenLastCalledWith("/articles/before", { scroll: false });
  replace.mockClear();
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowRight" });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight", altKey: true });
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight", repeat: true });
  expect(replace).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(back).toHaveBeenCalledOnce();
});
it("disables navigation at feed boundaries and when the article is outside the sequence", () => {
  const view = preview(["first"]);
  expect((screen.getByRole("button", { name: "Previous article" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Next article" }) as HTMLButtonElement).disabled).toBe(true);
  view.unmount();
  preview(["unrelated"]);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "ArrowRight" });
  expect(replace).not.toHaveBeenCalled();
});
it("loads the next feed page before navigating and does not reopen a dismissed preview", async () => {
  let done!: (slug: string) => void;
  const load = vi.fn(() => new Promise<string>(resolve => { done = resolve; }));
  preview(["first"], load, true);
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  expect(load).toHaveBeenCalledOnce();
  await act(async () => done("second"));
  expect(replace).toHaveBeenCalledWith("/articles/second", { scroll: false });
  replace.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Next article" }));
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  await act(async () => done("second"));
  expect(replace).not.toHaveBeenCalled();
});
it("keeps the original direct-route modal hidden when navigation returns to its slug", () => {
  render(<ArticleNavigationProvider><ArticleModal direct slug="first">Original direct preview</ArticleModal><ArticleModal>Routed preview</ArticleModal></ArticleNavigationProvider>);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Original direct preview")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close preview" }));
  expect(replace).toHaveBeenCalledWith("/");
});

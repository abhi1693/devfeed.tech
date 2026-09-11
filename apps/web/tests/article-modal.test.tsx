// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ArticleModal } from "@/components/article-modal";
const { back, replace } = vi.hoisted(() => ({
  back: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ back, replace }) }));
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

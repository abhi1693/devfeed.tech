// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseFilters } from "@/lib/feed-query";
import { UserSearch } from "@/components/user-search";

const router = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
beforeEach(() => router.replace.mockReset());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
it("focuses search without inserting /, then accepts normal typing and slashes", async () => {
  render(<UserSearch />);
  const user = userEvent.setup();
  await user.keyboard("/");
  const search = screen.getByRole("searchbox") as HTMLInputElement;
  expect(document.activeElement).toBe(search);
  expect(search.value).toBe("");
  await user.keyboard("react/router");
  expect(search.value).toBe("react/router");
});
it.each(["input", "textarea", "select", "editor", "textbox"])(
  "preserves / in %s controls",
  (kind) => {
    render(
      <>
        <UserSearch />
        {kind === "input" ? (
          <input data-testid="editor" />
        ) : kind === "textarea" ? (
          <textarea data-testid="editor" />
        ) : kind === "select" ? (
          <select data-testid="editor" />
        ) : (
          <div
            data-testid="editor"
            tabIndex={0}
            contentEditable={kind === "editor"}
            role={kind === "textbox" ? "textbox" : undefined}
          >
            <span>Editable</span>
          </div>
        )}
      </>,
    );
    const editor = screen.getByTestId("editor");
    editor.focus();
    expect(fireEvent.keyDown(editor.firstElementChild ?? editor, { key: "/" })).toBe(true);
    expect(document.activeElement).toBe(editor);
  },
);
it.each([
  { ctrlKey: true },
  { metaKey: true },
  { altKey: true },
  { isComposing: true },
  { repeat: true },
])("ignores modified/composing/repeated shortcuts %j", (options) => {
  render(<UserSearch />);
  expect(fireEvent.keyDown(document.body, { key: "/", ...options })).toBe(true);
  expect(document.activeElement).not.toBe(screen.getByRole("searchbox"));
});
it("respects prevented events and open modal focus", () => {
  const view = render(
    <>
      <UserSearch />
      <dialog open>
        <button>Close preview</button>
      </dialog>
    </>,
  );
  const close = screen.getByRole("button", { name: "Close preview" });
  close.focus();
  expect(fireEvent.keyDown(close, { key: "/" })).toBe(true);
  expect(document.activeElement).toBe(close);
  view.rerender(<UserSearch />);
  const event = new KeyboardEvent("keydown", { key: "/", bubbles: true, cancelable: true });
  event.preventDefault();
  document.dispatchEvent(event);
  expect(document.activeElement).not.toBe(screen.getByRole("searchbox"));
});
it("removes the listener when unmounted", () => {
  const view = render(<UserSearch />);
  view.unmount();
  expect(fireEvent.keyDown(document.body, { key: "/" })).toBe(true);
});

it("places the caret after the existing query when the shortcut is used again", async () => {
  render(<UserSearch />);
  const user = userEvent.setup();
  const search = screen.getByRole("searchbox") as HTMLInputElement;
  await user.click(search);
  await user.type(search, "react");
  search.setSelectionRange(0, 0);
  search.blur();
  await user.keyboard("/");
  expect(document.activeElement).toBe(search);
  expect(search.selectionStart).toBe(5);
  expect(search.selectionEnd).toBe(5);
  await user.keyboard(" router");
  expect(search.value).toBe("react router");
});

it("debounces typing, retains filters, and resets pagination without requiring Enter", () => {
  vi.useFakeTimers();
  render(
    <UserSearch
      filters={parseFilters({ topic: "python", content_type: "tutorial", cursor: "old-page" })}
    />,
  );
  const search = screen.getByRole("searchbox");
  fireEvent.change(search, { target: { value: "rea" } });
  vi.advanceTimersByTime(200);
  fireEvent.change(search, { target: { value: "react" } });
  vi.advanceTimersByTime(349);
  expect(router.replace).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(router.replace).toHaveBeenCalledOnce();
  const url = new URL(router.replace.mock.calls[0][0], "https://devfeed.test");
  expect(url.searchParams.get("q")).toBe("react");
  expect(url.pathname).toBe("/topics/python/tutorials");
  expect(url.searchParams.has("content_type")).toBe(false);
  expect(url.searchParams.has("cursor")).toBe(false);
  expect(router.replace.mock.calls[0][1]).toEqual({ scroll: false });
});
it("clearing search updates results and unmounting cancels queued work", () => {
  vi.useFakeTimers();
  const view = render(<UserSearch filters={parseFilters({ q: "react" })} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
  vi.advanceTimersByTime(350);
  expect(router.replace).toHaveBeenCalledWith("/", { scroll: false });
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "later" } });
  view.unmount();
  vi.advanceTimersByTime(350);
  expect(router.replace).toHaveBeenCalledOnce();
});

it("waits for composition to finish before searching", () => {
  vi.useFakeTimers();
  render(<UserSearch />);
  const search = screen.getByRole("searchbox");
  fireEvent.compositionStart(search);
  fireEvent.change(search, { target: { value: "日本語" } });
  vi.advanceTimersByTime(500);
  expect(router.replace).not.toHaveBeenCalled();
  fireEvent.compositionEnd(search);
  vi.advanceTimersByTime(350);
  expect(router.replace).toHaveBeenCalledOnce();
});

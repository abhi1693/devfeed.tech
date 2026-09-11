// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserSearch } from "@/components/user-search";

afterEach(cleanup);
it("focuses search without inserting /, then accepts normal typing and slashes", async () => {
  render(<UserSearch />);const user = userEvent.setup();
  await user.keyboard("/");const search = screen.getByRole("searchbox") as HTMLInputElement;
  expect(document.activeElement).toBe(search);expect(search.value).toBe("");
  await user.keyboard("react/router");expect(search.value).toBe("react/router");
});
it.each(["input", "textarea", "select", "editor", "textbox"])("preserves / in %s controls", kind => {
  render(<><UserSearch />{kind === "input" ? <input data-testid="editor" /> : kind === "textarea" ? <textarea data-testid="editor" /> : kind === "select" ? <select data-testid="editor" /> : <div data-testid="editor" tabIndex={0} contentEditable={kind === "editor"} role={kind === "textbox" ? "textbox" : undefined}><span>Editable</span></div>}</>);
  const editor = screen.getByTestId("editor");editor.focus();
  expect(fireEvent.keyDown(editor.firstElementChild ?? editor, { key: "/" })).toBe(true);
  expect(document.activeElement).toBe(editor);
});
it.each([{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { isComposing: true }, { repeat: true }])("ignores modified/composing/repeated shortcuts %j", options => {
  render(<UserSearch />);expect(fireEvent.keyDown(document.body, { key: "/", ...options })).toBe(true);
  expect(document.activeElement).not.toBe(screen.getByRole("searchbox"));
});
it("respects prevented events and open modal focus", () => {
  const view = render(<><UserSearch /><dialog open><button>Close preview</button></dialog></>);
  const close = screen.getByRole("button", { name: "Close preview" });close.focus();
  expect(fireEvent.keyDown(close, { key: "/" })).toBe(true);expect(document.activeElement).toBe(close);
  view.rerender(<UserSearch />);const event = new KeyboardEvent("keydown", { key: "/", bubbles: true, cancelable: true });event.preventDefault();document.dispatchEvent(event);
  expect(document.activeElement).not.toBe(screen.getByRole("searchbox"));
});
it("removes the listener when unmounted", () => {
  const view = render(<UserSearch />);view.unmount();expect(fireEvent.keyDown(document.body, { key: "/" })).toBe(true);
});

// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchField } from "@/components/molecules/search-field";

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });
const tick = (ms = 250) => act(() => vi.advanceTimersByTime(ms));

it("searches after typing pauses, without Enter, and keeps the focused input", () => {
  const search = vi.fn();
  const view = render(<SearchField label="Search topics" value="" onSearch={search} />);
  const input = screen.getByRole("textbox"); input.focus();
  fireEvent.change(input, { target: { value: "Re" } }); tick(150);
  fireEvent.change(input, { target: { value: "React " } }); tick(249);
  expect(search).not.toHaveBeenCalled(); tick(1);
  expect(search).toHaveBeenCalledExactlyOnceWith("React");
  view.rerender(<SearchField label="Search topics" value="React" onSearch={search} />);
  expect(document.activeElement).toBe(input);
  expect((input as HTMLInputElement).value).toBe("React ");
});

it("retains newer typing when an earlier URL update arrives and clears immediately", () => {
  const search = vi.fn();
  const view = render(<SearchField label="Search topics" value="" onSearch={search} />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "Re" } }); tick();
  fireEvent.change(input, { target: { value: "React" } });
  view.rerender(<SearchField label="Search topics" value="Re" onSearch={search} />);
  expect((input as HTMLInputElement).value).toBe("React"); tick();
  fireEvent.click(screen.getByRole("button", { name: "Clear search topics" }));
  expect(search.mock.calls).toEqual([["Re"], ["React"], [""]]);
  view.rerender(<SearchField label="Search topics" value="React" onSearch={search} />);
  expect((input as HTMLInputElement).value).toBe("");
  expect(document.activeElement).toBe(input);
});

it("cancels unsent searches on browser navigation, filter changes and unmount", () => {
  const search = vi.fn();
  const view = render(<SearchField label="Search" value="React" scopeKey="pending" onSearch={search} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Vue" } });
  view.rerender(<SearchField label="Search" value="Angular" scopeKey="pending" onSearch={search} />); tick();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Angular");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Vue" } });
  view.rerender(<SearchField label="Search" value="Angular" scopeKey="approved" onSearch={search} />); tick();
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("Angular");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Vue" } });
  view.unmount(); tick();
  expect(search).not.toHaveBeenCalled();
});

it("waits for IME composition and allows immediate Enter without duplicate searches", () => {
  const search = vi.fn();
  render(<SearchField label="Search" value="" onSearch={search} />);
  const input = screen.getByRole("textbox");
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "数据库" } }); tick(500);
  fireEvent.submit(screen.getByRole("search"));
  expect(search).not.toHaveBeenCalled();
  fireEvent.compositionEnd(input);
  fireEvent.submit(screen.getByRole("search")); tick(500);
  expect(search).toHaveBeenCalledExactlyOnceWith("数据库");
});

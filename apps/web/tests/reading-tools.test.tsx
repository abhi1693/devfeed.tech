// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReadingTools } from "@/components/reading-tools";

const state = vi.hoisted(() => ({ user: null as { user_id: string } | null, loaded: vi.fn() }));
vi.mock("@/components/user-account", () => ({ useUser: () => state }));
vi.mock("@/components/account-reading-tools", () => {
  state.loaded();
  return {
    AccountReadingTools: () => (
      <input aria-label="Reading tools" defaultValue={state.user?.user_id} />
    ),
  };
});
afterEach(() => {
  cleanup();
  state.user = null;
});

it("loads tools only for signed-in readers and discards the previous account's state", async () => {
  const view = render(<ReadingTools />);
  expect(state.loaded).not.toHaveBeenCalled();
  expect(screen.queryByRole("textbox")).toBeNull();
  state.user = { user_id: "first" };
  view.rerender(<ReadingTools />);
  const tools = await screen.findByRole("textbox", { name: "Reading tools" });
  expect(state.loaded).toHaveBeenCalledOnce();
  fireEvent.change(tools, { target: { value: "previous account state" } });
  view.rerender(<ReadingTools />);
  expect(screen.getByDisplayValue("previous account state")).toBeTruthy();
  state.user = { user_id: "second" };
  view.rerender(<ReadingTools />);
  expect(screen.getByDisplayValue("second")).toBeTruthy();
  state.user = null;
  view.rerender(<ReadingTools />);
  expect(screen.queryByRole("textbox")).toBeNull();
});

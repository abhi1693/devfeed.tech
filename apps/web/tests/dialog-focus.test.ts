// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cycleDialogFocus } from "@/lib/dialog-focus";

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

it("wraps focus both ways and ignores hidden, disabled and non-tabbable controls", () => {
  document.body.innerHTML = `<dialog open><button id="first">Close</button><a id="middle" href="/article">Read</a><button id="last">Save</button><button hidden>Hidden</button><button disabled>Disabled</button><button tabindex="-1">Programmatic focus</button><div inert><button>Inert</button></div></dialog>`;
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(function (
    this: HTMLElement,
  ) {
    return (this.hidden ? [] : [{}]) as unknown as DOMRectList;
  });
  const dialog = document.querySelector("dialog")!;
  const first = document.getElementById("first")!;
  const last = document.getElementById("last")!;
  last.focus();
  expect(cycleDialogFocus(dialog, false)).toBe(true);
  expect(document.activeElement).toBe(first);
  expect(cycleDialogFocus(dialog, true)).toBe(true);
  expect(document.activeElement).toBe(last);
  const middle = document.getElementById("middle")!;
  middle.focus();
  expect(cycleDialogFocus(dialog, false)).toBe(false);
  expect(cycleDialogFocus(dialog, true)).toBe(false);
  expect(document.activeElement).toBe(middle);
});

it("handles empty and single-control dialogs", () => {
  document.body.innerHTML = `<dialog open><button disabled>Unavailable</button></dialog>`;
  const dialog = document.querySelector("dialog")!;
  expect(cycleDialogFocus(dialog, false)).toBe(false);
  dialog.innerHTML = "<button>Close</button>";
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
  const button = dialog.querySelector("button")!;
  button.focus();
  expect(cycleDialogFocus(dialog, false)).toBe(true);
  expect(cycleDialogFocus(dialog, true)).toBe(true);
  expect(document.activeElement).toBe(button);
});

it("restores a preview trigger after an authorization check makes it focusable again", async () => {
  const { restoreDialogFocus } = await import("@/lib/dialog-focus");
  vi.useFakeTimers();
  try {
    document.body.innerHTML = '<button id="trigger">Article</button>';
    const target = document.getElementById("trigger")!;
    const focus = vi.spyOn(target, "focus").mockImplementationOnce(() => {});
    restoreDialogFocus(target);
    expect(document.activeElement).not.toBe(target);
    await vi.advanceTimersByTimeAsync(32);
    expect(document.activeElement).toBe(target);
    expect(focus).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});

it("does not steal focus after reader input or after the old trigger is removed", async () => {
  const { restoreDialogFocus } = await import("@/lib/dialog-focus");
  vi.useFakeTimers();
  try {
    for (const event of ["pointerdown", "keydown"]) {
      document.body.innerHTML = '<button id="trigger">Article</button>';
      const target = document.getElementById("trigger")!;
      const focus = vi.spyOn(target, "focus").mockImplementation(() => {});
      restoreDialogFocus(target);
      window.dispatchEvent(new Event(event));
      await vi.advanceTimersByTimeAsync(4000);
      expect(focus).toHaveBeenCalledTimes(1);
    }
    document.body.innerHTML = '<button id="trigger">Article</button>';
    const target = document.getElementById("trigger")!;
    const focus = vi.spyOn(target, "focus").mockImplementation(() => {});
    restoreDialogFocus(target);
    target.remove();
    await vi.advanceTimersByTimeAsync(32);
    expect(focus).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});

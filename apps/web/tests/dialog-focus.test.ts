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

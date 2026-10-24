// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { beginReaderNavigation, navigationStarted } from "@/lib/reader-navigation";
import { cancelDialogScrollPreparation, lockDialogScroll } from "@/lib/dialog-scroll";

let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;

beforeEach(() => {
  history.replaceState({}, "", "/latest");
  frames = new Map();
  nextFrame = 0;
  vi.stubGlobal("scrollX", 0);
  vi.stubGlobal("scrollY", 1478);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.spyOn(window, "scrollTo").mockImplementation((options) => {
    vi.stubGlobal("scrollX", (options as ScrollToOptions).left);
    vi.stubGlobal("scrollY", (options as ScrollToOptions).top);
  });
});

afterEach(() => {
  cancelDialogScrollPreparation();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.style.overflow = "";
});

function paint() {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(0);
}

it("cancels native motion at preview activation while its route is still loading", () => {
  const navigation = vi.fn(() => expect(window.scrollTo).toHaveBeenCalledOnce());
  window.addEventListener(navigationStarted, navigation, { once: true });
  beginReaderNavigation("/articles/slow-preview");
  expect(navigation).toHaveBeenCalledOnce();
  expect(document.body.style.overflow).toBe("");
  vi.stubGlobal("scrollY", 1481);
  paint();
  expect(window.scrollY).toBe(1478);
  expect(frames.size).toBe(0);
});

it.each(["pointerdown", "wheel", "touchstart", "keydown", "popstate", "pagehide"])(
  "cancels an obsolete preview correction when %s supersedes navigation",
  (event) => {
    beginReaderNavigation("/articles/slow-preview");
    window.dispatchEvent(new Event(event));
    vi.stubGlobal("scrollY", 900);
    paint();
    expect(window.scrollY).toBe(900);
    expect(window.scrollTo).toHaveBeenCalledOnce();
    expect(document.body.style.overflow).toBe("");
  },
);

it("does not restore preview coordinates after the reader chooses an ordinary page", () => {
  beginReaderNavigation("/articles/slow-preview");
  beginReaderNavigation("/topics");
  vi.stubGlobal("scrollY", 0);
  paint();
  expect(window.scrollY).toBe(0);
  expect(window.scrollTo).toHaveBeenCalledOnce();
});

it("adopts the matching activation position when a modal commits before the queued correction", () => {
  beginReaderNavigation("/articles/slow-preview");
  vi.stubGlobal("scrollY", 1481);
  const release = lockDialogScroll("slow-preview");
  expect(window.scrollY).toBe(1478);
  expect(frames.size).toBe(1);
  vi.stubGlobal("scrollY", 1481);
  paint();
  expect(window.scrollY).toBe(1478);
  release();
  expect(document.body.style.overflow).toBe("");
});

it("retains activation coordinates when a slow preview commits after the correction frame", () => {
  beginReaderNavigation("/articles/slow-preview");
  paint();
  // Native compositor work may finish after the first frame but before the route renders.
  vi.stubGlobal("scrollY", 1481);
  const release = lockDialogScroll("slow-preview");
  try {
    expect(window.scrollY).toBe(1478);
  } finally {
    release();
  }
});

it("respects new reader input after the activation correction while a preview is loading", () => {
  beginReaderNavigation("/articles/slow-preview");
  paint();
  window.dispatchEvent(new Event("wheel"));
  vi.stubGlobal("scrollY", 900);
  const release = lockDialogScroll("slow-preview");
  try {
    expect(window.scrollY).toBe(900);
  } finally {
    release();
  }
});

it("does not apply an unrelated preview activation to a different modal", () => {
  beginReaderNavigation("/articles/slow-preview");
  vi.stubGlobal("scrollY", 900);
  const release = lockDialogScroll("other-preview");
  paint();
  expect(window.scrollY).toBe(900);
  release();
});

it.each(["/sources", "https://other.example/articles/preview", "#main"])(
  "preserves ordinary navigation scrolling for %s",
  (href) => {
    beginReaderNavigation(href);
    expect(window.scrollTo).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  },
);

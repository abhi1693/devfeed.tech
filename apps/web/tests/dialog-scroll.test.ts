// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { lockDialogScroll } from "@/lib/dialog-scroll";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.style.overflow = "";
});

it("cancels in-flight document scrolling and corrects a queued browser frame at modal entry", () => {
  vi.stubGlobal("scrollX", 12);
  vi.stubGlobal("scrollY", 1478);
  const frames = new Map<number, FrameRequestCallback>();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(1, callback);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  const scroll = vi.spyOn(window, "scrollTo").mockImplementation((options) => {
    vi.stubGlobal("scrollX", (options as ScrollToOptions).left);
    vi.stubGlobal("scrollY", (options as ScrollToOptions).top);
  });
  document.body.style.overflow = "auto";

  const release = lockDialogScroll();
  expect(document.body.style.overflow).toBe("hidden");
  expect(scroll).toHaveBeenCalledWith({ left: 12, top: 1478, behavior: "instant" });
  // A compositor update can land after overflow has blocked further wheel input.
  vi.stubGlobal("scrollY", 1481);
  frames.get(1)!(0);
  expect(window.scrollY).toBe(1478);
  expect(scroll).toHaveBeenCalledTimes(2);

  release();
  expect(document.body.style.overflow).toBe("auto");
});

it("does not restore stale coordinates after a preview closes before the next browser frame", () => {
  vi.stubGlobal("scrollX", 0);
  vi.stubGlobal("scrollY", 1478);
  const frames = new Map<number, FrameRequestCallback>();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(1, callback);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  const scroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});

  const release = lockDialogScroll();
  release();
  expect(frames.size).toBe(0);
  expect(scroll).toHaveBeenCalledOnce();
  expect(document.body.style.overflow).toBe("");
});

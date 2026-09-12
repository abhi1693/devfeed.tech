// jsdom does not implement media queries; individual theme tests override matches.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (media: string) =>
      Object.assign(new EventTarget(), { matches: false, media }),
  });
}
export {};
import { beforeEach, vi } from "vitest";

beforeEach(() => { if (typeof document !== "undefined") vi.spyOn(document, "hasFocus").mockReturnValue(true); });

if (typeof window !== "undefined") {
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, writable: true, value: class { observe() {} unobserve() {} disconnect() {} } });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, writable: true, value() {} });
}

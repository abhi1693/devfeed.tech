import { beforeEach, vi } from "vitest";

// jsdom has no focused browser window; tests start in an active tab by default.
beforeEach(() => { if (typeof document !== "undefined") vi.spyOn(document, "hasFocus").mockReturnValue(true); });

// Layout APIs used by the Radix/cmdk primitives are not implemented in jsdom.
if (typeof window !== "undefined") {
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, writable: true, value: class {
    observe() {} unobserve() {} disconnect() {}
  } });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, writable: true, value() {} });
}

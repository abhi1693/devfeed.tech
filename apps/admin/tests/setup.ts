// Layout APIs used by the Radix/cmdk primitives are not implemented in jsdom.
if (typeof window !== "undefined") {
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, writable: true, value: class {
    observe() {} unobserve() {} disconnect() {}
  } });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, writable: true, value() {} });
}

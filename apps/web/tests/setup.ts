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

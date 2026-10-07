// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { readerRequest } from "@/lib/reader-runtime";

vi.mock("@/lib/reader-runtime", () => ({ readerRequest: vi.fn() }));
let intersection: IntersectionObserverCallback;
let element: HTMLAnchorElement;
let dispose: (() => void) | undefined;
function view(ratio: number) {
  intersection(
    [
      {
        target: element,
        time: performance.now(),
        boundingClientRect: element.getBoundingClientRect(),
        intersectionRect: element.getBoundingClientRect(),
        rootBounds: null,
        isIntersecting: ratio > 0,
        intersectionRatio: ratio,
      } as IntersectionObserverEntry,
    ],
    {} as IntersectionObserver,
  );
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(readerRequest).mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        intersection = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  element = document.createElement("a");
  element.href = "https://partner.example/product";
  document.body.append(element);
});
afterEach(() => {
  dispose?.();
  dispose = undefined;
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
it("requires half visibility for a continuous second and records each kind independently", async () => {
  dispose = observePartnerPlacement(element, "receipt");
  view(0.49);
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).not.toHaveBeenCalled();
  view(0.5);
  await vi.advanceTimersByTimeAsync(999);
  expect(readerRequest).not.toHaveBeenCalled();
  view(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).not.toHaveBeenCalled();
  view(0.5);
  await vi.advanceTimersByTimeAsync(1000);
  element.dispatchEvent(new MouseEvent("click"));
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  expect(
    vi.mocked(readerRequest).mock.calls.map(([, init]) => JSON.parse(init!.body as string)),
  ).toEqual([
    { receipt: "receipt", kind: "impression" },
    { receipt: "receipt", kind: "click" },
  ]);
  expect(vi.mocked(readerRequest).mock.calls[0][1]).toMatchObject({
    credentials: "omit",
    keepalive: true,
    referrerPolicy: "no-referrer",
  });
});
it("counts a click independently of an impression, including middle clicks", async () => {
  dispose = observePartnerPlacement(element, "receipt");
  element.dispatchEvent(new MouseEvent("auxclick", { button: 2 }));
  expect(readerRequest).not.toHaveBeenCalled();
  element.dispatchEvent(new MouseEvent("auxclick", { button: 1 }));
  await vi.advanceTimersByTimeAsync(1);
  expect(JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string).kind).toBe("click");
});
it("hidden tabs, detached placements and cleanup cancel impression timing", async () => {
  dispose = observePartnerPlacement(element, "receipt");
  view(1);
  await vi.advanceTimersByTimeAsync(500);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).not.toHaveBeenCalled();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  element.remove();
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).not.toHaveBeenCalled();
  document.body.append(element);
  view(1);
  dispose();
  await vi.advanceTimersByTimeAsync(1000);
  element.dispatchEvent(new MouseEvent("click"));
  expect(readerRequest).not.toHaveBeenCalled();
});
it("retries with the same receipt and does not propagate network errors", async () => {
  vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
  dispose = observePartnerPlacement(element, "receipt");
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  expect(vi.mocked(readerRequest).mock.calls[0][1]!.body).toBe(
    vi.mocked(readerRequest).mock.calls[1][1]!.body,
  );
});
it("does not retry invalid receipts or attempt tracking without a receipt", async () => {
  vi.mocked(readerRequest).mockResolvedValue(new Response(null, { status: 403 }));
  dispose = observePartnerPlacement(element, "");
  element.dispatchEvent(new MouseEvent("click"));
  expect(readerRequest).not.toHaveBeenCalled();
  dispose = observePartnerPlacement(element, "receipt");
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

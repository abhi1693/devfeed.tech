// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { readerRequest } from "@/lib/reader-runtime";
import { partnerReceiptExpiresAt } from "@/lib/partner-receipts";

vi.mock("@/lib/reader-runtime", () => ({ readerRequest: vi.fn() }));
const NOW = 1_800_000_000_000;
const HOUR = 3_600_000;
function deliveryReceipt(issuedAt = NOW) {
  return `v1.${"1".repeat(32)}.${"2".repeat(32)}.${Math.floor(issuedAt / 1000)}.${"3".repeat(64)}`;
}
let receipt: string;
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
  vi.setSystemTime(NOW);
  receipt = deliveryReceipt();
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
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, receipt, onView);
  view(0.49);
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(onView).not.toHaveBeenCalled();
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
  expect(onView).toHaveBeenCalledTimes(1);
  expect(
    vi.mocked(readerRequest).mock.calls.map(([, init]) => JSON.parse(init!.body as string)),
  ).toEqual([
    { receipt, kind: "impression" },
    { receipt, kind: "click" },
  ]);
  expect(vi.mocked(readerRequest).mock.calls[0][1]).toMatchObject({
    credentials: "omit",
    keepalive: true,
    referrerPolicy: "no-referrer",
  });
});
it("counts a click independently of an impression, including middle clicks", async () => {
  dispose = observePartnerPlacement(element, receipt);
  element.dispatchEvent(new MouseEvent("auxclick", { button: 2 }));
  expect(readerRequest).not.toHaveBeenCalled();
  element.dispatchEvent(new MouseEvent("auxclick", { button: 1 }));
  await vi.advanceTimersByTimeAsync(1);
  expect(JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string).kind).toBe("click");
});
it("tracks sponsored article visibility while counting only article destination clicks", async () => {
  const card = document.createElement("article");
  const bookmark = document.createElement("button");
  const link = document.createElement("a");
  link.className = "card-open-link";
  card.append(bookmark, link);
  document.body.append(card);
  dispose = observePartnerPlacement(
    card,
    receipt,
    undefined,
    undefined,
    "a.card-open-link, a.article-read-link",
  );
  bookmark.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  card.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  expect(readerRequest).not.toHaveBeenCalled();
  link.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    receipt,
    kind: "click",
  });
});
it("hidden tabs, detached placements and cleanup cancel impression timing", async () => {
  dispose = observePartnerPlacement(element, receipt);
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
  dispose = observePartnerPlacement(element, receipt);
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
  dispose = observePartnerPlacement(element, receipt);
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("pauses an unviewed background card and resumes the full viewability interval", async () => {
  const state = { completed: new Set<string>(), pending: new Set<string>(), viewed: false };
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, receipt, onView, state);
  view(1);
  await vi.advanceTimersByTimeAsync(500);
  dispose();
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(onView).not.toHaveBeenCalled();
  dispose = observePartnerPlacement(element, receipt, onView, state);
  view(1);
  await vi.advanceTimersByTimeAsync(999);
  expect(readerRequest).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(onView).toHaveBeenCalledTimes(1);
});

it("restores an already viewed card without another impression or local cap increment", async () => {
  const state = { completed: new Set<string>(), pending: new Set<string>(), viewed: false };
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, receipt, onView, state);
  view(1);
  await vi.advanceTimersByTimeAsync(1000);
  dispose();
  dispose = observePartnerPlacement(element, receipt, onView, state);
  view(1);
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(onView).toHaveBeenCalledTimes(1);
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[1][1]?.body)).kind).toBe("click");
});

it("uses signed issuance seconds for the exact one-hour receipt deadline", () => {
  expect(partnerReceiptExpiresAt(deliveryReceipt(NOW - 123_000))).toBe(NOW + HOUR - 123_000);
});

it.each([
  "",
  "opaque-receipt",
  deliveryReceipt().replace("v1.", "v2."),
  deliveryReceipt().replace("1800000000", "1800000000.5"),
  deliveryReceipt().replace("1800000000", "-1800000000"),
  deliveryReceipt().replace("1800000000", "1000000000000"),
  deliveryReceipt().replace("1".repeat(32), "A".repeat(32)),
  deliveryReceipt().replace("3".repeat(64), "3".repeat(63)),
  `${deliveryReceipt()}\n`,
])("rejects malformed receipt framing before binding tracking: %j", (invalid) => {
  expect(partnerReceiptExpiresAt(invalid)).toBe(0);
  dispose = observePartnerPlacement(element, invalid);
  element.dispatchEvent(new MouseEvent("click"));
  expect(readerRequest).not.toHaveBeenCalled();
});

it("stops clicks on a continuously mounted card at the receipt deadline", async () => {
  dispose = observePartnerPlacement(element, receipt);
  view(1);
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);

  vi.setSystemTime(NOW + HOUR);
  const click = new MouseEvent("click", { cancelable: true });
  element.dispatchEvent(click);
  element.dispatchEvent(new MouseEvent("auxclick", { button: 1 }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(click.defaultPrevented).toBe(false);
});

it("does not record an impression when its viewability interval ends at expiry", async () => {
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, deliveryReceipt(NOW - HOUR + 1000), onView);
  view(1);
  await vi.advanceTimersByTimeAsync(999);
  expect(readerRequest).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(onView).not.toHaveBeenCalled();
});

it("rechecks the deadline when a throttled foreground impression timer resumes", async () => {
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, receipt, onView);
  view(1);
  vi.setSystemTime(NOW + HOUR);
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(onView).not.toHaveBeenCalled();
});

it("does not resume expired tracking when a background tab becomes visible", async () => {
  const onView = vi.fn();
  dispose = observePartnerPlacement(element, receipt, onView);
  view(1);
  await vi.advanceTimersByTimeAsync(500);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));

  vi.setSystemTime(NOW + HOUR);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  view(1);
  element.dispatchEvent(new MouseEvent("click"));
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(onView).not.toHaveBeenCalled();
});

it.each(["network", "429", "503"])(
  "suppresses a %s retry that crosses the receipt deadline",
  async (failure) => {
    if (failure === "network") vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
    else
      vi.mocked(readerRequest).mockResolvedValueOnce(
        new Response(null, { status: Number(failure) }),
      );
    const state = { completed: new Set<string>(), pending: new Set<string>(), viewed: false };
    dispose = observePartnerPlacement(element, receipt, undefined, state);
    vi.setSystemTime(NOW + HOUR - 500);
    element.dispatchEvent(new MouseEvent("click"));
    await vi.advanceTimersByTimeAsync(1000);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    expect(state.pending.size).toBe(0);
    expect(state.completed.size).toBe(0);
  },
);

it("suppresses an in-flight retry after observer cleanup", async () => {
  let fail: (reason: Error) => void;
  vi.mocked(readerRequest).mockReturnValueOnce(
    new Promise<Response>((_, reject) => {
      fail = reject;
    }),
  );
  const state = { completed: new Set<string>(), pending: new Set<string>(), viewed: false };
  dispose = observePartnerPlacement(element, receipt, undefined, state);
  element.dispatchEvent(new MouseEvent("click"));
  expect(readerRequest).toHaveBeenCalledTimes(1);
  dispose();
  fail!(new Error("offline"));
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(state.pending.size).toBe(0);
});

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
let listeners: {
  owner: HTMLElement;
  type: string;
  listener: EventListenerOrEventListenerObject;
}[];

// JSDOM cannot create trusted input. Exercise registered listeners with browser-shaped
// events; the built web and extension browser tests prove real mouse/keyboard activation.
function activate(target: HTMLElement = element, overrides: Partial<MouseEvent> = {}) {
  const event = {
    type: "click",
    button: 0,
    isTrusted: true,
    target,
    ...overrides,
  } as MouseEvent;
  for (const { owner, type, listener } of listeners) {
    if (type !== event.type || !owner.contains(target)) continue;
    if (typeof listener === "function") listener.call(owner, event);
    else listener.handleEvent(event);
  }
}
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
  listeners = [];
  const addEventListener = HTMLElement.prototype.addEventListener;
  vi.spyOn(HTMLElement.prototype, "addEventListener").mockImplementation(function (
    this: HTMLElement,
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions,
  ) {
    listeners.push({ owner: this, type, listener });
    addEventListener.call(this, type, listener, options);
  });
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
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
it("requires half visibility for a continuous second and records each kind independently", async () => {
  dispose = observePartnerPlacement(element, receipt);
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
  activate();
  activate();
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(2);
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
  activate(element, { type: "auxclick", button: 2 });
  expect(readerRequest).not.toHaveBeenCalled();
  activate(element, { type: "auxclick", button: 1 });
  await vi.advanceTimersByTimeAsync(1);
  expect(JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string).kind).toBe("click");
});
it("ignores script-generated activations and unsupported mouse buttons", async () => {
  element.addEventListener("click", (event) => {
    if (!event.isTrusted) event.preventDefault();
  });
  dispose = observePartnerPlacement(element, receipt);
  element.click();
  element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  element.dispatchEvent(new MouseEvent("auxclick", { button: 1, bubbles: true }));
  activate(element, { button: 1 });
  activate(element, { button: 2 });
  activate(element, { type: "auxclick", button: 0 });
  activate(element, { type: "auxclick", button: 2 });
  expect(readerRequest).not.toHaveBeenCalled();
  activate();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});
it.each([
  { detail: 0 },
  { ctrlKey: true },
  { metaKey: true },
  { shiftKey: true },
  { defaultPrevented: true },
])("accepts trusted keyboard, modified and internal-navigation clicks: %j", async (activation) => {
  dispose = observePartnerPlacement(element, receipt);
  activate(element, activation);
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    receipt,
    kind: "click",
  });
});
it.each(["inert", "hidden", "aria-hidden"])(
  "does not count an activation inside a %s ancestor",
  async (attribute) => {
    const wrapper = document.createElement("div");
    document.body.append(wrapper);
    wrapper.append(element);
    wrapper.setAttribute(attribute, attribute === "aria-hidden" ? "true" : "");
    dispose = observePartnerPlacement(element, receipt);
    activate();
    expect(readerRequest).not.toHaveBeenCalled();
    wrapper.removeAttribute(attribute);
    activate();
    await vi.advanceTimersByTimeAsync(1);
    expect(readerRequest).toHaveBeenCalledTimes(1);
  },
);
it("rejects clicks from a hidden tab, detached placement, missing href or outside destination", async () => {
  dispose = observePartnerPlacement(element, receipt);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  activate();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  element.remove();
  activate();
  document.body.append(element);
  element.removeAttribute("href");
  activate();
  element.href = "https://partner.example/product";
  const outside = document.createElement("a");
  outside.href = "https://unrelated.example";
  document.body.append(outside);
  activate(element, { target: outside });
  expect(readerRequest).not.toHaveBeenCalled();
  activate();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});
it("tracks sponsored article visibility while counting only article destination clicks", async () => {
  const card = document.createElement("article");
  const bookmark = document.createElement("button");
  const link = document.createElement("a");
  link.className = "card-open-link";
  link.href = "/articles/sponsored-story";
  card.append(bookmark, link);
  document.body.append(card);
  dispose = observePartnerPlacement(
    card,
    receipt,
    undefined,
    "a.card-open-link, a.article-read-link",
  );
  activate(bookmark);
  activate(card);
  expect(readerRequest).not.toHaveBeenCalled();
  activate(link);
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
    receipt,
    kind: "click",
  });
});
it.each(["card-open-link", "article-read-link"])(
  "counts nested targets in sponsored article %s destinations once",
  async (className) => {
    const card = document.createElement("article");
    const unrelated = document.createElement("a");
    unrelated.href = "/topics/javascript";
    const link = document.createElement("a");
    link.className = className;
    link.href = className === "card-open-link" ? "/articles/story" : "https://source.example";
    const icon = document.createElement("span");
    link.append(icon);
    card.append(unrelated, link);
    document.body.append(card);
    dispose = observePartnerPlacement(
      card,
      receipt,
      undefined,
      "a.card-open-link, a.article-read-link",
    );
    activate(unrelated);
    link.setAttribute("inert", "");
    activate(icon);
    expect(readerRequest).not.toHaveBeenCalled();
    link.removeAttribute("inert");
    activate(icon);
    activate(icon, { type: "auxclick", button: 1 });
    await vi.advanceTimersByTimeAsync(1);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toEqual({
      receipt,
      kind: "click",
    });
  },
);
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
  activate();
  expect(readerRequest).not.toHaveBeenCalled();
});
it("retries with the same receipt and does not propagate network errors", async () => {
  vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
  dispose = observePartnerPlacement(element, receipt);
  activate();
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  expect(vi.mocked(readerRequest).mock.calls[0][1]!.body).toBe(
    vi.mocked(readerRequest).mock.calls[1][1]!.body,
  );
});
it("does not retry invalid receipts or attempt tracking without a receipt", async () => {
  vi.mocked(readerRequest).mockResolvedValue(new Response(null, { status: 403 }));
  dispose = observePartnerPlacement(element, "");
  activate();
  expect(readerRequest).not.toHaveBeenCalled();
  dispose = observePartnerPlacement(element, receipt);
  activate();
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("pauses a background card and resumes the full viewability interval", async () => {
  const state = { completed: new Set<string>(), pending: new Set<string>() };
  dispose = observePartnerPlacement(element, receipt, state);
  view(1);
  await vi.advanceTimersByTimeAsync(500);
  dispose();
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).not.toHaveBeenCalled();
  dispose = observePartnerPlacement(element, receipt, state);
  view(1);
  await vi.advanceTimersByTimeAsync(999);
  expect(readerRequest).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("restores an already measured card without another impression", async () => {
  const state = { completed: new Set<string>(), pending: new Set<string>() };
  dispose = observePartnerPlacement(element, receipt, state);
  view(1);
  await vi.advanceTimersByTimeAsync(1000);
  dispose();
  dispose = observePartnerPlacement(element, receipt, state);
  view(1);
  await vi.advanceTimersByTimeAsync(2000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  activate();
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
  activate();
  expect(readerRequest).not.toHaveBeenCalled();
});

it("stops clicks on a continuously mounted card at the receipt deadline", async () => {
  dispose = observePartnerPlacement(element, receipt);
  view(1);
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);

  vi.setSystemTime(NOW + HOUR);
  const preventDefault = vi.fn();
  activate(element, { preventDefault });
  activate(element, { type: "auxclick", button: 1 });
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(preventDefault).not.toHaveBeenCalled();
});

it("does not record an impression when its viewability interval ends at expiry", async () => {
  dispose = observePartnerPlacement(element, deliveryReceipt(NOW - HOUR + 1000));
  view(1);
  await vi.advanceTimersByTimeAsync(999);
  expect(readerRequest).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).not.toHaveBeenCalled();
});

it("rechecks the deadline when a throttled foreground impression timer resumes", async () => {
  dispose = observePartnerPlacement(element, receipt);
  view(1);
  vi.setSystemTime(NOW + HOUR);
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).not.toHaveBeenCalled();
});

it("does not resume expired tracking when a background tab becomes visible", async () => {
  dispose = observePartnerPlacement(element, receipt);
  view(1);
  await vi.advanceTimersByTimeAsync(500);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));

  vi.setSystemTime(NOW + HOUR);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  view(1);
  activate();
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).not.toHaveBeenCalled();
});

it.each(["network", "429", "503"])(
  "suppresses an accepted click's %s retry after cleanup when its receipt expires",
  async (failure) => {
    if (failure === "network") vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
    else
      vi.mocked(readerRequest).mockResolvedValueOnce(
        new Response(null, { status: Number(failure) }),
      );
    const state = { completed: new Set<string>(), pending: new Set<string>() };
    dispose = observePartnerPlacement(element, receipt, state);
    vi.setSystemTime(NOW + HOUR - 500);
    activate();
    dispose();
    await vi.advanceTimersByTimeAsync(1000);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    expect(state.pending.size).toBe(0);
    expect(state.completed.size).toBe(0);
  },
);

it("completes an in-flight accepted click after navigation without starting another event", async () => {
  let finish: (response: Response) => void;
  vi.mocked(readerRequest).mockReturnValueOnce(
    new Promise<Response>((resolve) => {
      finish = resolve;
    }),
  );
  const state = { completed: new Set<string>(), pending: new Set<string>() };
  dispose = observePartnerPlacement(element, receipt, state);
  activate();
  expect(readerRequest).toHaveBeenCalledTimes(1);
  dispose();
  activate();
  finish!(new Response(null, { status: 204 }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(readerRequest).toHaveBeenCalledTimes(1);
  expect(state.pending.size).toBe(0);
  expect([...state.completed]).toEqual(["click"]);
});

it.each(["network", "429", "503"])(
  "finishes an accepted click's bounded %s retry across article preview pause and resume",
  async (failure) => {
    let fail: () => void;
    vi.mocked(readerRequest).mockReturnValueOnce(
      new Promise<Response>((resolve, reject) => {
        fail = () => {
          if (failure === "network") reject(new Error("offline"));
          else resolve(new Response(null, { status: Number(failure) }));
        };
      }),
    );
    const state = { completed: new Set<string>(), pending: new Set<string>() };
    dispose = observePartnerPlacement(element, receipt, state);
    activate();
    const firstBody = vi.mocked(readerRequest).mock.calls[0][1]?.body;
    dispose();
    activate();
    expect(readerRequest).toHaveBeenCalledTimes(1);
    fail!();
    dispose = observePartnerPlacement(element, receipt, state);
    activate();
    await vi.advanceTimersByTimeAsync(999);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(readerRequest).toHaveBeenCalledTimes(2);
    expect(vi.mocked(readerRequest).mock.calls[1][1]?.body).toBe(firstBody);
    expect(vi.mocked(readerRequest).mock.calls[1][1]).toMatchObject({
      credentials: "omit",
      keepalive: true,
      referrerPolicy: "no-referrer",
    });
    expect(state.pending.size).toBe(0);
    expect([...state.completed]).toEqual(["click"]);
    activate();
    await vi.advanceTimersByTimeAsync(2000);
    expect(readerRequest).toHaveBeenCalledTimes(2);
  },
);

it.each(["network", "429", "503"])(
  "still cancels an impression's %s retry after observer cleanup",
  async (failure) => {
    let fail: () => void;
    vi.mocked(readerRequest).mockReturnValueOnce(
      new Promise<Response>((resolve, reject) => {
        fail = () => {
          if (failure === "network") reject(new Error("offline"));
          else resolve(new Response(null, { status: Number(failure) }));
        };
      }),
    );
    const state = { completed: new Set<string>(), pending: new Set<string>() };
    dispose = observePartnerPlacement(element, receipt, state);
    view(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    dispose();
    fail!();
    await vi.advanceTimersByTimeAsync(1000);
    expect(readerRequest).toHaveBeenCalledTimes(1);
    expect(state.pending.size).toBe(0);
    expect(state.completed.size).toBe(0);
  },
);

it("tracks viewable impressions and clicks without accessing browser storage", async () => {
  const local = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
    throw new Error("local storage must not be accessed");
  });
  const session = vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new Error("session storage must not be accessed");
  });
  dispose = observePartnerPlacement(element, receipt);
  view(1);
  await vi.advanceTimersByTimeAsync(1000);
  activate();
  await vi.advanceTimersByTimeAsync(1);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  expect(local).not.toHaveBeenCalled();
  expect(session).not.toHaveBeenCalled();
});

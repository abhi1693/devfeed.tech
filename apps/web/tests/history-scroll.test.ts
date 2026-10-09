// @vitest-environment jsdom
import assert from "node:assert/strict";
import { it as test } from "vitest";
import { startHistoryScroll, saveHistoryScroll } from "@/lib/history-scroll";

function fixture(back = false) {
  const saved = new Map<string, PropertyDescriptor | undefined>();
  const set = (name: string, value: unknown) => {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { configurable: true, value });
  };
  const window = Object.assign(new EventTarget(), {
    history: {} as typeof history,
    scrollX: 0,
    scrollY: 0,
    scrollTo: (() => {}) as (options: { left: number; top: number }) => void,
  });
  const history = {
    state: {
      readerBackground: "/latest?language=fr",
      ...(back ? { readerScroll: [0, 650] } : {}),
    } as Record<string, unknown>,
    scrollRestoration: "auto",
    replaceState(state: Record<string, unknown>) {
      this.state = state;
    },
  };
  let maxScroll = 0;
  let now = 0;
  const frames = new Map<number, () => void>();
  let nextFrame = 0;
  window.history = history;
  window.scrollX = 0;
  window.scrollY = 0;
  window.scrollTo = ({ left, top }) => {
    window.scrollX = left;
    window.scrollY = Math.min(maxScroll, top);
    window.dispatchEvent(new Event("scroll"));
  };
  set("window", window);
  set("history", history);
  set("performance", {
    now: () => now,
    getEntriesByType: () => [{ type: back ? "back_forward" : "navigate" }],
  });
  for (const name of ["scrollX", "scrollY"] as const) {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { configurable: true, get: () => window[name] });
  }
  set("requestAnimationFrame", (callback: () => void) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  set("cancelAnimationFrame", (id: number) => frames.delete(id));
  const stop = startHistoryScroll();
  return {
    window,
    history,
    frames,
    height: (value: number) => (maxScroll = value),
    tick: () => {
      now += 16;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((fn) => fn());
    },
    cleanup: () => {
      stop();
      for (const [name, descriptor] of saved) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else Reflect.deleteProperty(globalThis, name);
      }
    },
  };
}

test("restores entry coordinates after asynchronous content grows without storing reader data", () => {
  const f = fixture();
  try {
    f.history.state = { readerBackground: "/latest?language=fr", readerScroll: [0, 650] };
    f.window.dispatchEvent(new Event("popstate"));
    f.tick();
    f.tick();
    assert.equal(f.window.scrollY, 0);
    assert.deepEqual(
      f.history.state.readerScroll,
      [0, 650],
      "clamped scroll events cannot overwrite destination",
    );
    f.height(2000);
    f.tick();
    assert.equal(f.window.scrollY, 650);
    assert.equal(f.frames.size, 0);
    f.window.scrollY = 900;
    saveHistoryScroll();
    assert.deepEqual(f.history.state, {
      readerBackground: "/latest?language=fr",
      readerScroll: [0, 900],
    });
  } finally {
    f.cleanup();
  }
});

test("user input or a newer navigation cancels restoration, and teardown restores native behavior", () => {
  for (const event of [
    "wheel",
    "touchstart",
    "keydown",
    "pointerdown",
    "devfeed:extension-route",
  ]) {
    const f = fixture();
    try {
      f.history.state = { readerScroll: [0, 650] };
      f.window.dispatchEvent(new Event("popstate"));
      f.tick();
      f.window.dispatchEvent(new Event(event));
      f.height(2000);
      f.tick();
      assert.equal(f.window.scrollY, 0, event);
      assert.equal(f.frames.size, 0);
    } finally {
      f.cleanup();
    }
    assert.equal(f.history.scrollRestoration, "auto");
  }
});

test("ignores malformed coordinates and bounds retries when content remains short", () => {
  const f = fixture();
  try {
    for (const position of [null, [0, -1], [0, Infinity], [0, "650"], [0]]) {
      f.history.state = { readerScroll: position };
      f.window.dispatchEvent(new Event("popstate"));
      assert.equal(f.frames.size, 0);
    }
    f.history.state = { readerScroll: [0, 650] };
    f.window.dispatchEvent(new Event("popstate"));
    for (let i = 0; i < 200; i++) f.tick();
    assert.equal(f.frames.size, 0);
  } finally {
    f.cleanup();
  }
});

test("restores saved coordinates when a new document loads from browser history", () => {
  const f = fixture(true);
  try {
    assert.deepEqual(f.history.state.readerScroll, [0, 650]);
    f.height(2000);
    f.tick();
    f.tick();
    assert.equal(f.window.scrollY, 650);
  } finally {
    f.cleanup();
  }
});

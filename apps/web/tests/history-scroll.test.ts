// @vitest-environment jsdom
import assert from "node:assert/strict";
import { it as test, vi } from "vitest";
import { startHistoryScroll, saveHistoryScroll } from "@/lib/history-scroll";

function fixture(navigation = "navigate", position: unknown = [0, 650]) {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
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
      ...(navigation !== "navigate" ? { readerScroll: position } : {}),
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
    getEntriesByType: () => [{ type: navigation }],
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
    stop,
    height: (value: number) => (maxScroll = value),
    tick: () => {
      now += 16;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((fn) => fn());
    },
    cleanup: () => {
      stop();
      vi.restoreAllMocks();
      vi.useRealTimers();
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

test.each(["back_forward", "reload"])("restores saved coordinates on %s", (navigation) => {
  const f = fixture(navigation);
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

test("bounds history writes during sustained scrolling and saves the latest position", () => {
  const f = fixture();
  const writes = vi.spyOn(f.history, "replaceState");
  try {
    for (let i = 1; i <= 1000; i++) {
      f.window.scrollY = i;
      f.window.dispatchEvent(new Event("scroll"));
      vi.advanceTimersByTime(10);
    }
    vi.advanceTimersByTime(500);
    assert.ok(writes.mock.calls.length <= 20);
    assert.deepEqual(f.history.state, {
      readerBackground: "/latest?language=fr",
      readerScroll: [0, 1000],
    });
    f.window.dispatchEvent(new Event("scroll"));
    vi.advanceTimersByTime(500);
    assert.ok(writes.mock.calls.length <= 20, "unchanged coordinates need no history write");
  } finally {
    f.cleanup();
  }
});

test.each(["pagehide", "devfeed:reader-navigation"])(
  "flushes pending coordinates before %s",
  (event) => {
    const f = fixture();
    try {
      f.window.scrollY = 650;
      f.window.dispatchEvent(new Event("scroll"));
      assert.deepEqual(f.history.state.readerScroll, [0, 0]);
      f.window.dispatchEvent(new Event(event));
      assert.deepEqual(f.history.state.readerScroll, [0, 650]);
      assert.equal(vi.getTimerCount(), 0);
    } finally {
      f.cleanup();
    }
  },
);

test.each(["popstate", "devfeed:extension-route"])(
  "does not write pending coordinates into the destination after %s",
  (event) => {
    const f = fixture();
    try {
      f.window.scrollY = 650;
      f.window.dispatchEvent(new Event("scroll"));
      f.history.state = { readerScroll: [0, 1100] };
      f.window.dispatchEvent(new Event(event));
      vi.advanceTimersByTime(500);
      assert.deepEqual(f.history.state.readerScroll, [0, 1100]);
      assert.equal(vi.getTimerCount(), 0);
      if (event === "popstate") {
        f.height(2000);
        f.tick();
        f.tick();
        assert.equal(f.window.scrollY, 1100);
      }
    } finally {
      f.cleanup();
    }
  },
);

test.each(["SecurityError", "QuotaExceededError"])(
  "tolerates rejected history saves (%s) and resumes when writes succeed",
  (name) => {
    const f = fixture();
    const writes = vi.spyOn(f.history, "replaceState").mockImplementation(() => {
      throw new DOMException("History update rejected", name);
    });
    try {
      f.window.scrollY = 650;
      assert.doesNotThrow(saveHistoryScroll);
      f.window.dispatchEvent(new Event("scroll"));
      assert.doesNotThrow(() => vi.advanceTimersByTime(500));
      assert.doesNotThrow(() => f.window.dispatchEvent(new Event("devfeed:reader-navigation")));
      assert.deepEqual(f.history.state.readerScroll, [0, 0]);
      writes.mockRestore();
      saveHistoryScroll();
      assert.deepEqual(f.history.state.readerScroll, [0, 650]);
    } finally {
      f.cleanup();
    }
  },
);

test("ignores invalid saved coordinates on reload and cancels pending saves on teardown", () => {
  const f = fixture("reload", [0, -1]);
  try {
    assert.equal(f.frames.size, 0);
    f.window.scrollY = 650;
    f.window.dispatchEvent(new Event("scroll"));
    assert.equal(vi.getTimerCount(), 1);
    f.stop();
    assert.equal(vi.getTimerCount(), 0);
    vi.advanceTimersByTime(500);
    assert.deepEqual(f.history.state.readerScroll, [0, -1]);
  } finally {
    f.cleanup();
  }
});

import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import {
  retentionBaselineReady,
  stableRetentionPosition,
} from "../../../scripts/testing/preview-feed-retention.mjs";

function ready({ count = 25, unhydrated = -1, placeholder = false, fonts = "loaded" } = {}) {
  const rows = Array.from({ length: count }, (_, index) => ({
    checkVisibility: () => true,
    querySelector: () => (index === unhydrated ? null : {}),
  }));
  return runInNewContext(`(${retentionBaselineReady})(".article-row")`, {
    document: {
      querySelectorAll: () => rows,
      querySelector: () => (placeholder ? {} : null),
      fonts: { status: fonts },
    },
  });
}

test("retention measurements wait for every paginated row's engagement hydration", () => {
  assert.equal(ready(), true);
  assert.equal(ready({ count: 24 }), false);
  assert.equal(ready({ unhydrated: 24 }), false);
});

test("retention measurements wait for placeholder exit and downloaded fonts", () => {
  assert.equal(ready({ placeholder: true }), false);
  assert.equal(ready({ fonts: "loading" }), false);
  assert.equal(ready(), true);
});

function stablePosition(positionAtFrame) {
  let now = 0;
  let frame = 0;
  const callbacks = [];
  const promise = runInNewContext(`(${stableRetentionPosition})(node)`, {
    node: { getBoundingClientRect: () => ({ y: positionAtFrame(frame), height: 24 }) },
    document: { documentElement: { scrollHeight: 5000 } },
    scrollY: 2500,
    performance: { now: () => now },
    requestAnimationFrame: (callback) => callbacks.push(callback),
  });
  while (callbacks.length) {
    now += 16;
    frame += 1;
    callbacks.shift()();
  }
  return { promise, elapsed: now };
}

test("the baseline follows a late layout change before taking a reading-position sample", async () => {
  const { promise, elapsed } = stablePosition((frame) => (frame < 7 ? 365.25 : 338.25));
  assert.deepEqual(Array.from(await promise), [338.25, 24, 2500, 5000]);
  assert.ok(elapsed >= 7 * 16 + 200);
});

test("an unsettled baseline fails instead of hiding continued layout motion", async () => {
  const { promise, elapsed } = stablePosition((frame) => frame);
  await assert.rejects(promise, /Retention baseline did not settle/);
  assert.ok(elapsed >= 5000);
});

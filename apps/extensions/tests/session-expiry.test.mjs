import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [new URL("../src/session-expiry.ts", import.meta.url).pathname],
  bundle: true,
  write: false,
  format: "cjs",
});

function clock() {
  let now = 0;
  let nextId = 0;
  const pending = new Map();
  const module = { exports: {} };
  runInNewContext(outputFiles[0].text, {
    module,
    exports: module.exports,
    Date: { now: () => now },
    setTimeout: (callback, delay) => {
      const id = ++nextId;
      // Model the browser's signed 32-bit delay conversion, including overflow.
      pending.set(id, { callback, at: now + Math.max(0, delay | 0) });
      return id;
    },
    clearTimeout: (id) => pending.delete(id),
  });
  return {
    schedule: module.exports.scheduleSessionExpiry,
    advance(milliseconds) {
      const end = now + milliseconds;
      for (;;) {
        const next = [...pending.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > end) break;
        pending.delete(next[0]);
        now = next[1].at;
        next[1].callback();
      }
      now = end;
    },
  };
}

test("a 30-day session stays signed in past the timer limit and expires at its deadline", () => {
  const time = clock();
  const lifetime = 30 * 24 * 60 * 60;
  let expired = 0;
  time.schedule(lifetime, () => expired++);
  time.advance(2 ** 31 - 1);
  assert.equal(expired, 0);
  time.advance(lifetime * 1000 - (2 ** 31 - 1) - 1);
  assert.equal(expired, 0);
  time.advance(1);
  assert.equal(expired, 1);
  time.advance(1000);
  assert.equal(expired, 1);
});

test("short and already-expired sessions clear at their actual deadline", () => {
  const time = clock();
  let expired = 0;
  time.schedule(2, () => expired++);
  time.advance(1999);
  assert.equal(expired, 0);
  time.advance(1);
  assert.equal(expired, 1);
  time.schedule(1, () => expired++);
  assert.equal(expired, 2);
});

test("changing or unmounting a session cancels the rearmed timer", () => {
  const time = clock();
  let expired = 0;
  const cancel = time.schedule(30 * 24 * 60 * 60, () => expired++);
  time.advance(2 ** 31 - 1);
  cancel();
  time.advance(30 * 24 * 60 * 60 * 1000);
  assert.equal(expired, 0);
});

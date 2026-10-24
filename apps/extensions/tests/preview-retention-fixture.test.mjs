import assert from "node:assert/strict";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { JSDOM } from "jsdom";
import {
  retentionBaselineReady,
  stableRetentionPosition,
} from "../../../scripts/testing/preview-feed-retention.mjs";

function ready({
  count = 25,
  unhydrated = -1,
  placeholder = false,
  fonts = "loaded",
  layout = "stacked",
  guest = false,
  missingBookmark = false,
} = {}) {
  const compact = layout === "compact";
  const rows = Array.from({ length: count }, (_, index) => {
    const bookmark = missingBookmark
      ? ""
      : guest
        ? '<a class="bookmark-button" href="https://devfeed.tech/login">Sign in to save</a>'
        : `<button class="bookmark-button" ${index === unhydrated ? "disabled" : ""} aria-pressed="true">Remove bookmark</button>`;
    if (!compact)
      return `<article class="article-row ${layout === "cards" ? "article-card" : "article-stacked-row"}">${index === unhydrated ? "" : '<span class="engagement-count">0</span>'}${bookmark}</article>`;
    return `<tr class="article-row article-list-row"><td class="bookmark-column">${bookmark}</td></tr>`;
  }).join("");
  const dom = new JSDOM(
    `${compact ? `<table><tbody>${rows}</tbody></table>` : rows}${placeholder ? '<div class="reader-loading-placeholder"></div>' : ""}`,
  );
  dom.window.Element.prototype.checkVisibility = () => true;
  Object.defineProperty(dom.window.document, "fonts", { value: { status: fonts } });
  try {
    return runInNewContext(`(${retentionBaselineReady})(".article-row")`, {
      document: dom.window.document,
    });
  } finally {
    dom.window.close();
  }
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

test("compact guest rows are ready with bookmark links and no engagement counters", () => {
  assert.equal(ready({ layout: "compact", guest: true }), true);
  assert.equal(ready({ layout: "compact", guest: true, missingBookmark: true }), false);
});

test("compact member rows wait until every bookmark button finishes hydrating", () => {
  assert.equal(ready({ layout: "compact" }), true);
  assert.equal(ready({ layout: "compact", unhydrated: 24 }), false);
  assert.equal(ready({ layout: "compact", missingBookmark: true }), false);
});

for (const layout of ["stacked", "cards"])
  test(`${layout} guest bookmark links do not bypass counter hydration`, () => {
    assert.equal(ready({ layout, guest: true }), true);
    assert.equal(ready({ layout, guest: true, unhydrated: 24 }), false);
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

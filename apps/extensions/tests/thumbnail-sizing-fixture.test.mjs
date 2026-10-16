import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { checkThumbnailSizing } from "../../../scripts/testing/thumbnail-sizing.mjs";

test("thumbnail measurement excludes requests from the previous document", async () => {
  const page = new EventEmitter();
  const asset = (index) => `https://images.example.test/thumbnails/v2/fixture-${index}/640.webp`;
  const request = (index) => ({ resourceType: () => "image", url: () => asset(index) });
  page.goto = async (href) => {
    if (href === "about:blank") page.emit("request", request(0));
    else for (const index of [0, 1]) page.emit("request", request(index));
  };
  page.context = () => ({
    newCDPSession: async () => ({ send: async () => {}, detach: async () => {} }),
  });
  page.viewportSize = () => ({ width: 800, height: 900 });
  page.waitForFunction = async () => {};
  page.locator = () => ({
    first: () => ({ waitFor: async () => {} }),
    nth: (index) => ({
      scrollIntoViewIfNeeded: async () => {},
      evaluate: async () => ({
        currentSrc: asset(index),
        naturalWidth: 640,
        naturalHeight: 360,
        renderedWidth: 350,
        renderedHeight: (350 * 9) / 16,
        dpr: 1.75,
        sizes: index ? "auto, 350px" : "350px",
        loading: index ? "lazy" : "eager",
        priority: index ? "auto" : "high",
      }),
    }),
  });
  const results = await checkThumbnailSizing(page, "https://reader.example/latest");
  assert.deepEqual(
    results.map(({ selected }) => selected),
    [640, 640],
  );
  assert.equal(page.listenerCount("request"), 0);
});

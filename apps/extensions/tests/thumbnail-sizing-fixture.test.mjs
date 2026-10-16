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

for (const failPlaceholder of [false, true]) {
  test(`failed-image checks preserve context routes and other pages (${failPlaceholder ? "failure" : "success"})`, async () => {
    const page = new EventEmitter();
    const existing = { pattern: "https://images.example.test/thumbnails/v2/**", handler: () => {} };
    const handlers = [existing];
    const removed = [];
    let aborted = 0;
    let continued = 0;
    const context = {
      newCDPSession: async () => ({ send: async () => {}, detach: async () => {} }),
      route: async (pattern, handler) => handlers.push({ pattern, handler }),
      unroute: async (pattern, handler) => {
        const index = handlers.findIndex(
          (item) => item.pattern === pattern && item.handler === handler,
        );
        assert.ok(index > 0, "cleanup must only remove the temporary handler");
        removed.push(handlers.splice(index, 1)[0]);
      },
    };
    const asset = (index) => `https://images.example.test/thumbnails/v2/fixture-${index}/480.webp`;
    page.context = () => context;
    page.viewportSize = () => ({ width: 1350, height: 940 });
    page.goto = async (href) => {
      if (href !== "about:blank")
        for (const index of [0, 1])
          page.emit("request", { resourceType: () => "image", url: () => asset(index) });
    };
    page.waitForFunction = async () => {};
    page.locator = (selector) => ({
      first: () => ({
        waitFor: async () => {
          if (!selector.includes("image-placeholder")) return;
          const handler = handlers.at(-1).handler;
          for (const target of [page, {}])
            await handler({
              request: () => ({ frame: () => ({ page: () => target }) }),
              abort: async () => aborted++,
              fallback: async () => continued++,
            });
          if (failPlaceholder) throw new Error("Missing failed-image placeholder");
        },
        locator: () => ({ count: async () => 0 }),
      }),
      nth: (index) => ({
        scrollIntoViewIfNeeded: async () => {},
        evaluate: async () => ({
          currentSrc: asset(index),
          naturalWidth: 480,
          naturalHeight: 270,
          renderedWidth: 350,
          renderedHeight: (350 * 9) / 16,
          dpr: 1,
          sizes: index ? "auto, 350px" : "480px",
          loading: index ? "lazy" : "eager",
          priority: index ? "auto" : "high",
        }),
      }),
    });
    if (failPlaceholder)
      await assert.rejects(
        checkThumbnailSizing(page, "https://reader.example/latest"),
        /Missing failed-image placeholder/,
      );
    else await checkThumbnailSizing(page, "https://reader.example/latest");
    assert.equal(aborted, 1, "only the checked page's thumbnails fail");
    assert.equal(continued, 1, "other pages retain their image mocks");
    assert.equal(removed.length, 1);
    assert.deepEqual(handlers, [existing]);
  });
}

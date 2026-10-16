import assert from "node:assert/strict";
import test from "node:test";
import sharp from "sharp";
import { mockManagedImages } from "../../../scripts/testing/managed-images.mjs";

test("thumbnail responses are ready before a context can close", async () => {
  let handler;
  await mockManagedImages({
    route: async (_pattern, callback) => {
      handler = callback;
    },
  });
  for (const width of [320, 480, 640, 768, 960]) {
    let closed = false;
    let body;
    const pending = handler({
      request: () => ({
        url: () => `https://images.example.test/thumbnails/v2/fixture-${width}/${width}.webp`,
      }),
      fulfill: (response) => {
        assert.equal(closed, false, "no image encoding remains after interception begins");
        body = response.body;
        return Promise.resolve();
      },
    });
    closed = true;
    await pending;
    const metadata = await sharp(body).metadata();
    assert.equal(metadata.width, width);
    assert.equal(metadata.height, (width * 9) / 16);
  }
});

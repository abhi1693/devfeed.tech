import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

/** Check the declared icons through the browser, including CSP and image decoding. */
export async function checkAppIcons(page, { apple = true } = {}) {
  for (const [rel, size] of [["icon", 32], ...(apple ? [["apple-touch-icon", 180]] : [])]) {
    const link = page.locator(`head link[rel="${rel}"]`);
    assert.equal(await link.count(), 1, `One ${rel} declaration`);
    assert.equal(await link.getAttribute("type"), "image/png");
    assert.equal(await link.getAttribute("sizes"), `${size}x${size}`);
    const loaded = await link.evaluate(async (node) => {
      const response = await fetch(node.href);
      const bytes = [...new Uint8Array(await response.arrayBuffer())];
      const image = new Image();
      image.src = node.href;
      await image.decode();
      return {
        status: response.status,
        type: response.headers.get("content-type"),
        bytes,
        width: image.naturalWidth,
        height: image.naturalHeight,
      };
    });
    assert.equal(loaded.status, 200);
    assert.match(loaded.type, /^image\/png(?:;|$)/);
    assert.equal(loaded.width, size);
    assert.equal(loaded.height, size);
    const expected = await readFile(
      new URL(`../../packages/theme/assets/devfeed-icon-${size}.png`, import.meta.url),
    );
    assert.deepEqual(Buffer.from(loaded.bytes), expected, `${rel} uses the shared DevFeed mark`);
  }
}

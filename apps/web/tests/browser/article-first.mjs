import assert from "node:assert/strict";

export function blockedFeed(fail = false) {
  let resume, started;
  const pending = new Promise((resolve) => {
    resume = resolve;
  });
  const firstRequest = new Promise((resolve) => {
    started = resolve;
  });
  return {
    fail,
    released: false,
    firstRequest,
    async wait(path) {
      if (!/^\/(?:api\/)?v1\/feed(?:\/options)?$/.test(path)) return false;
      started();
      await pending;
      return true;
    },
    release() {
      this.released = true;
      resume();
    },
  };
}

export async function checkBrowserIcons(page, apple = false) {
  for (const [rel, size, budget] of [
    ["icon", 32, 2048],
    ...(apple ? [["apple-touch-icon", 180, 10_240]] : []),
  ]) {
    const asset = await page.evaluate(async (relation) => {
      const link = document.head.querySelector(`link[rel="${relation}"]`);
      const response = await fetch(link.href);
      const bytes = new Uint8Array(await response.arrayBuffer());
      const dimensions = new DataView(bytes.buffer);
      return {
        status: response.status,
        declaredSize: link.sizes.value,
        signature: [...bytes.slice(0, 8)],
        width: dimensions.getUint32(16),
        height: dimensions.getUint32(20),
        bytes: bytes.length,
      };
    }, rel);
    assert.equal(asset.status, 200);
    assert.deepEqual(asset.signature, [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(asset.declaredSize, `${size}x${size}`);
    assert.equal(asset.width, size);
    assert.equal(asset.height, size);
    assert.ok(asset.bytes <= budget, `${rel} transfers ${asset.bytes} bytes (budget ${budget})`);
  }
}

/** Article content must be visible and interactive while feed requests remain blocked. */
export async function checkArticleFirst(page, href, gate, { apple = false, screenshot } = {}) {
  let timer;
  try {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(href, { waitUntil: "commit", timeout: 10_000 });
    await page
      .locator("dialog.article-modal[open] #article-preview-title")
      .waitFor({ timeout: 5000 });
    await Promise.race([
      gate.firstRequest,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("No blocked background feed request")), 5000);
      }),
    ]);
    clearTimeout(timer);
    assert.equal(gate.released, false, "article renders before the feed completes");
    const title = await page.locator("#article-preview-title").textContent();
    assert.ok(title.trim());
    assert.equal(
      await page.getByRole("button", { name: "Close preview", exact: true }).isEnabled(),
      true,
    );
    await checkBrowserIcons(page, apple);
    if (screenshot) await page.screenshot({ path: screenshot, animations: "disabled" });
    if (gate.fail) {
      gate.release();
      await page.getByRole("heading", { name: "Couldn’t load the feed", exact: true }).waitFor();
      assert.equal(await page.locator("#article-preview-title").textContent(), title);
      assert.equal(await page.locator("dialog.article-modal[open]").count(), 1);
    } else {
      await page.getByRole("button", { name: "Close preview", exact: true }).click();
      await page
        .locator("dialog.article-modal[open]")
        .waitFor({ state: "detached", timeout: 1000 });
      assert.equal(gate.released, false, "dismissal does not wait for the background feed");
      gate.release();
      await page.locator(".article-card").first().waitFor();
    }
  } finally {
    clearTimeout(timer);
    gate.release();
  }
}

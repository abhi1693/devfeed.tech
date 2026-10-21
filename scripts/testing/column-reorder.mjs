import assert from "node:assert/strict";

const frames = (page) =>
  page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );

/** Use the same drag handle with actual keyboard or mouse input in every reader runtime. */
export async function reorderColumn(
  page,
  column,
  offset,
  { input = "keyboard", cancel = false, settle = true, onPhase = async () => {} } = {},
) {
  assert.ok(Number.isInteger(offset));
  const handle = column.getByRole("button", { name: /^Reorder .+ column$/ });
  await onPhase("before-reveal");
  await handle.scrollIntoViewIfNeeded();
  await onPhase("revealed");
  if (input === "keyboard") {
    await handle.focus();
    await page.keyboard.press("Space");
    await column.locator("[data-column-drag-handle]").waitFor();
    await page.waitForFunction(
      (node) => node.dataset.dragging === "true",
      await column.elementHandle(),
    );
    for (let index = 0; index < Math.abs(offset); index++) {
      await page.keyboard.press(offset < 0 ? "ArrowLeft" : "ArrowRight");
      await frames(page);
    }
    await page.keyboard.press(cancel ? "Escape" : "Space");
  } else {
    assert.equal(input, "pointer");
    const targetId = await column.evaluate((node, offset) => {
      const siblings = [...node.parentElement.querySelectorAll(":scope > [data-column-id]")];
      const index = siblings.indexOf(node);
      return siblings[Math.max(0, Math.min(siblings.length - 1, index + offset))].dataset.columnId;
    }, offset);
    const target = page.locator(`[data-column-id="${targetId}"]`);
    const start = await handle.boundingBox();
    const destination = await target.boundingBox();
    const from = { x: start.x + start.width / 2, y: start.y + start.height / 2 };
    const to = { x: destination.x + destination.width / 2, y: from.y };
    assert.ok(
      to.x >= 0 && to.x <= (await page.evaluate(() => innerWidth)),
      "Pointer target is visible",
    );
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    try {
      await page.mouse.move(from.x + (offset < 0 ? -10 : 10), from.y, { steps: 2 });
      await page.waitForFunction(
        (node) => node.dataset.dragging === "true",
        await column.elementHandle(),
      );
      await onPhase("lifted");
      await page.mouse.move(to.x, to.y, { steps: 16 });
      await frames(page);
      await onPhase("moved");
      if (cancel) await page.keyboard.press("Escape");
    } finally {
      await page.mouse.up();
    }
  }
  await page.waitForFunction(
    (node) => node.dataset.dragging !== "true",
    await column.elementHandle(),
  );
  await frames(page);
  await onPhase("dropped");
  if (settle) await page.locator('[aria-label="Columns workspace"][aria-busy="false"]').waitFor();
  await onPhase("settled");
}

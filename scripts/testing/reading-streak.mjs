import assert from "node:assert/strict";

export async function checkReadingStreak(page, screenshot) {
  const original = page.viewportSize();
  const trigger = page.getByRole("button", { name: "Reading streak: 2 days", exact: true });
  await trigger.waitFor();
  await trigger.click();
  const panel = page.getByRole("dialog", { name: "Your reading rhythm" });
  await panel.waitFor();
  assert.equal(await panel.getByText("Today counted", { exact: true }).count(), 1);
  assert.equal(await panel.getByText("Best streak", { exact: true }).count(), 1);
  await panel.screenshot({ path: `${screenshot}-desktop.png` });
  await page.keyboard.press("Escape");
  await panel.waitFor({ state: "hidden" });
  assert.equal(await trigger.evaluate((node) => node === document.activeElement), true);
  for (const width of [375, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await trigger.click();
    await panel.waitFor();
    const bounds = await panel.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.screenshot({ path: `${screenshot}-${width}.png` });
    await panel.getByRole("button", { name: "Close reading streak" }).click();
  }
  await page.setViewportSize(original);
}

import assert from "node:assert/strict";

export async function checkFeaturedOn(page) {
  const section = page.locator(".sidebar-featured");
  const toggle = page.getByRole("button", { name: "Expand sidebar", exact: true });
  if (await toggle.isVisible()) await toggle.click();
  await section.waitFor({ state: "visible" });
  assert.equal(
    await section.locator("a").getAttribute("href"),
    "https://nicklaunches.com/products/devfeed/",
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (value) => document.documentElement.classList.toggle("dark", value === "dark"),
      theme,
    );
    const badge = section.locator(`.featured-${theme}`);
    await badge.waitFor({ state: "visible" });
    assert.ok(
      await badge.evaluate(async (img) => {
        await img.decode();
        return img.naturalWidth > 0;
      }),
    );
    const bounds = await badge.boundingBox();
    const parent = await section.boundingBox();
    assert.ok(bounds.x >= parent.x && bounds.x + bounds.width <= parent.x + parent.width + 1);
  }
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  await section.waitFor({ state: "hidden" });
}

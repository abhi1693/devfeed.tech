import assert from "node:assert/strict";

export const longFilterSource = {
  id: "88888888-8888-4888-8888-888888888888",
  slug: "filter-source",
  name: "Unicode 日本語 — international engineering, architecture, and dependable applications journal",
  logo_url: null,
};

export async function assertSelectMenu(page, label) {
  const menu = page.locator(".shared-select-popover:visible");
  await menu.waitFor();
  const snapshot = await menu.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return {
      x: box.x,
      right: box.right,
      width: innerWidth,
      scrollWidth: node.scrollWidth,
      clientWidth: node.clientWidth,
      horizontalScrollers: [...node.querySelectorAll("*")]
        .filter(
          (item) =>
            ["auto", "scroll"].includes(getComputedStyle(item).overflowX) &&
            item.scrollWidth > item.clientWidth + 1,
        )
        .map((item) => item.className),
      labels: [...node.querySelectorAll(".shared-select-option-label")].map((item) => {
        const rect = item.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(item);
        return {
          text: item.textContent,
          lines: new Set([...range.getClientRects()].map((line) => Math.round(line.top))).size,
          x: rect.x,
          right: rect.right,
          clientWidth: item.clientWidth,
          scrollWidth: item.scrollWidth,
        };
      }),
      checks: [...node.querySelectorAll("[data-checked]")].map((item) => {
        const rect = item.getBoundingClientRect();
        return { x: rect.x, right: rect.right, width: rect.width };
      }),
    };
  });
  assert.ok(
    snapshot.x >= 0 && snapshot.right <= snapshot.width + 1,
    `${label} stays inside the viewport`,
  );
  assert.ok(
    snapshot.scrollWidth <= snapshot.clientWidth + 1,
    `${label} has no horizontal overflow`,
  );
  assert.deepEqual(snapshot.horizontalScrollers, [], `${label} has no horizontal scrollbar`);
  for (const item of snapshot.labels) {
    assert.ok(
      item.x >= snapshot.x - 1 && item.right <= snapshot.right + 1,
      `${label}: option text remains inside the menu`,
    );
    if (item.text.length <= 20) {
      assert.equal(item.lines, 1, `${label}: ${item.text} remains one line`);
      assert.ok(
        item.scrollWidth <= item.clientWidth + 1,
        `${label}: ${item.text} remains fully visible`,
      );
    }
  }
  for (const item of snapshot.checks)
    assert.ok(
      item.width >= 15.5 && item.x >= snapshot.x && item.right <= snapshot.right + 1,
      `${label}: checkmarks remain visible`,
    );
}

export async function checkSelectMenus(page, labels) {
  const viewport = page.viewportSize();
  try {
    for (const width of [320, 375, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const label of labels) {
        const trigger = page.getByRole("combobox", { name: label, exact: true });
        await trigger.waitFor();
        await trigger.focus();
        await page.keyboard.press("Enter");
        await assertSelectMenu(page, `${label} at ${width}px`);
        await page.keyboard.press("Escape");
        await page.locator(".shared-select-popover:visible").waitFor({ state: "hidden" });
        const handle = await trigger.elementHandle();
        try {
          await page.waitForFunction((node) => node === document.activeElement, handle);
        } finally {
          await handle.dispose();
        }
      }
    }
  } finally {
    await page.setViewportSize(viewport);
  }
}

export async function checkSourceFilter(page, target, source, output) {
  const viewport = page.viewportSize();
  try {
    await page.goto(target);
    await page.locator('button[aria-label="Grid view"]:not(:disabled)').waitFor();
    await page.locator(".filter-menu > summary").click();
    await checkSelectMenus(page, ["Source"]);
    for (const width of [320, 375, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      const panel = await page.locator(".filter-popover").evaluate((node) => {
        const box = node.getBoundingClientRect();
        return { x: box.x, right: box.right, width: innerWidth };
      });
      assert.ok(
        panel.x >= 0 && panel.right <= panel.width + 1,
        `The parent filter panel stays within the ${width}px viewport`,
      );
      await page.getByRole("combobox", { name: "Source", exact: true }).click();
      await assertSelectMenu(page, `Source at ${width}px`);
      await page.getByRole("option", { name: longFilterSource.name, exact: true }).waitFor();
      if (output)
        await page.screenshot({ path: `${output}-source-${width}.png`, animations: "disabled" });
      // cmdk supplies aria-labelledby from the surrounding choice label, so
      // locate its actual input within the named open source options.
      await page.locator(".shared-select-popover input[cmdk-input]").fill("日本語");
      await page.getByRole("option", { name: longFilterSource.name, exact: true }).waitFor();
      assert.equal(
        await page.getByRole("option").count(),
        1,
        "Source search filters Unicode labels",
      );
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await page.locator(".shared-select-popover:visible").waitFor({ state: "hidden" });
      assert.equal(
        await page.getByRole("combobox", { name: "Source", exact: true }).textContent(),
        longFilterSource.name,
        "Keyboard selection keeps the full source label",
      );
    }
    await page.getByRole("combobox", { name: "Source", exact: true }).click();
    await page.locator(".shared-select-popover input[cmdk-input]").fill(source.name);
    await page.getByRole("option", { name: source.name, exact: true }).click();
    await page.getByRole("button", { name: "Apply filters", exact: true }).click();
    await page.waitForURL(
      new RegExp(
        `/sources/${source.slug}(?:/(?:articles|news|tutorials|releases|comparisons|opinions))?(?:[?#]|$)`,
      ),
    );
    await page.locator(".article-card, .article-list-row").first().waitFor();
  } finally {
    await page.setViewportSize(viewport);
    await page.goto(target);
  }
}

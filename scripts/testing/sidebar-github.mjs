import assert from "node:assert/strict";

export async function checkSidebarGitHub(page, screenshot) {
  const link = page.locator(".sidebar").getByRole("link", {
    name: "Star us on GitHub (opens in a new tab)",
    exact: true,
  });
  assert.equal(await link.getAttribute("href"), "https://github.com/abhi1693/devfeed.tech");
  assert.equal(await link.getAttribute("target"), "_blank");
  assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(await link.getAttribute("title"), "Star us on GitHub");
  assert.equal(
    await page.locator('.topbar a[href="https://github.com/abhi1693/devfeed.tech"]').count(),
    0,
  );
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  await page.waitForFunction(
    (node) =>
      Math.abs(
        node.getBoundingClientRect().width - node.parentElement.getBoundingClientRect().width,
      ) < 1,
    await link.elementHandle(),
  );
  assert.equal(await link.locator("span").textContent(), "Star us on GitHub");
  assert.equal(await link.locator("span").evaluate((node) => getComputedStyle(node).opacity), "1");
  assert.equal(await link.evaluate((node) => node.scrollWidth > node.clientWidth), false);
  if (screenshot) await page.screenshot({ path: screenshot });
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  assert.equal(await link.isVisible(), true);
}

import assert from "node:assert/strict";

export async function checkExtensionInstall(page, browser, placement, screenshot) {
  const label = `Get for ${browser === "edge" ? "Edge" : "Chrome"}`;
  const button = page.getByRole("link", { name: label, exact: true });
  await button.waitFor();
  const container = page.locator(placement);
  assert.equal(await container.locator("a").first().innerText(), label);
  assert.equal(await button.getAttribute("target"), "_blank");
  assert.match(await button.getAttribute("rel"), /noopener/);
  await page.waitForFunction(() => {
    const image = document.querySelector(".extension-install-button img");
    return image?.complete && image.naturalWidth > 0;
  });
  const href = await button.getAttribute("href");
  assert.ok(
    href.startsWith(
      browser === "edge"
        ? "https://microsoftedge.microsoft.com/addons/"
        : "https://chromewebstore.google.com/detail/",
    ),
  );
  await page
    .context()
    .route(href, (route) =>
      route.fulfill({ body: "Extension store fixture", contentType: "text/html" }),
    );
  const popupPromise = page.waitForEvent("popup");
  await button.click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  assert.equal(popup.url(), href);
  await popup.close();
  const neighbor = container.locator(
    placement === ".feed-toolbar-actions" ? "summary" : 'a[href="/settings/sources"]',
  );
  const appearance = (element) => {
    const style = getComputedStyle(element);
    return {
      height: element.getBoundingClientRect().height,
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      color: style.color,
      background: style.backgroundColor,
      border: style.borderColor,
      radius: style.borderRadius,
    };
  };
  const originalDark = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const dark of [false, true]) {
    await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), dark);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(200); // Allow the existing theme/hover transitions to settle.
    assert.deepEqual(await button.evaluate(appearance), await neighbor.evaluate(appearance));
    await page.screenshot({
      path: dark ? screenshot.replace(/\.png$/, "-dark.png") : screenshot,
      fullPage: true,
    });
    await button.hover();
    await page.waitForTimeout(200);
    const hover = await button.evaluate(appearance);
    await neighbor.hover();
    await page.waitForTimeout(200);
    assert.deepEqual(hover, await neighbor.evaluate(appearance));
  }
  await page.evaluate(
    (value) => document.documentElement.classList.toggle("dark", value),
    originalDark,
  );
  await page.mouse.move(0, 0);
}

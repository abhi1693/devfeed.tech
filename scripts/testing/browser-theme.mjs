import assert from "node:assert/strict";

export async function checkBrowserTheme(page, screenshotPrefix, { bootstrap = false } = {}) {
  const original = await page.evaluate(() => localStorage.getItem("devfeed:theme"));
  const toggle = page.locator(".theme-toggle");
  const mode = async (name, dark) => {
    await page.getByRole("button", { name: new RegExp(`^Theme: ${name}\\.`) }).waitFor();
    await page.waitForFunction(
      (value) => document.documentElement.classList.contains("dark") === value,
      dark,
    );
  };
  try {
    await page.emulateMedia({ colorScheme: "light" });
    await page.evaluate(() => {
      localStorage.removeItem("devfeed:theme");
      window.dispatchEvent(new StorageEvent("storage", { key: "devfeed:theme", newValue: null }));
    });
    await mode("System", false);
    await page.emulateMedia({ colorScheme: "dark" });
    await mode("System", true);
    await toggle.click(); // System -> Light
    await mode("Light", false);
    const light = await page
      .locator("body")
      .evaluate((node) => getComputedStyle(node).backgroundColor);
    await toggle.click(); // Light -> Dark
    await mode("Dark", true);
    assert.equal(await page.evaluate(() => localStorage.getItem("devfeed:theme")), "dark");
    await page.waitForFunction(
      (color) => getComputedStyle(document.body).backgroundColor !== color,
      light,
    );
    const dark = await page
      .locator("body")
      .evaluate((node) => getComputedStyle(node).backgroundColor);
    assert.notEqual(dark, light, "The shared preference changes the rendered color scheme");
    if (screenshotPrefix) await page.screenshot({ path: `${screenshotPrefix}-dark.png` });
    await page.reload();
    await mode("Dark", true);
    await page.emulateMedia({ colorScheme: "light" });
    await mode("Dark", true);

    if (bootstrap) {
      const beforeHydration = await page.context().newPage();
      try {
        await beforeHydration.route(/\/_next\/static\/.*\.js(?:\?.*)?$/, (route) => route.abort());
        await beforeHydration.goto(page.url(), { waitUntil: "domcontentloaded" });
        assert.equal(
          await beforeHydration.evaluate(() => document.documentElement.classList.contains("dark")),
          true,
          "Saved dark mode applies before reader hydration",
        );
      } finally {
        await beforeHydration.close();
      }
    }

    const otherTab = await page.context().newPage();
    try {
      await otherTab.goto(page.url());
      await otherTab.locator(".theme-toggle").waitFor();
      await otherTab.evaluate(() => localStorage.setItem("devfeed:theme", "light"));
      await mode("Light", false);
      await page.emulateMedia({ colorScheme: "dark" });
      await mode("Light", false);
      await otherTab.evaluate(() => localStorage.removeItem("devfeed:theme"));
      await mode("System", true);
    } finally {
      await otherTab.close();
    }
    await page.bringToFront();
    await toggle.click(); // System -> Light
    await mode("Light", false);
    if (screenshotPrefix) await page.screenshot({ path: `${screenshotPrefix}-light.png` });
  } finally {
    await page.emulateMedia({ colorScheme: null });
    await page.evaluate((value) => {
      if (value === null) localStorage.removeItem("devfeed:theme");
      else localStorage.setItem("devfeed:theme", value);
      window.dispatchEvent(new StorageEvent("storage", { key: "devfeed:theme", newValue: value }));
    }, original);
  }
}

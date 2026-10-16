import assert from "node:assert/strict";

const key = "devfeed:theme";
const label = (mode) =>
  `Theme: ${mode[0].toUpperCase() + mode.slice(1)}. Switch to ${{ system: "light", light: "dark", dark: "system" }[mode]} theme`;

async function expectTheme(page, mode, dark) {
  await page.getByRole("button", { name: label(mode), exact: true }).waitFor();
  await page.waitForFunction(
    (expected) => document.documentElement.classList.contains("dark") === expected,
    dark,
  );
  assert.equal(
    await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
    dark ? "dark" : "light",
  );
}

async function assertDarkPortal(locator) {
  await locator.waitFor({ state: "visible" });
  await settleAnimations(locator);
  const surface = await locator.evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    insideWorkspace: Boolean(element.closest(".partner-workspace")),
    opacity: getComputedStyle(element).opacity,
  }));
  assert.equal(surface.insideWorkspace, false, "The menu is actually portalled outside the shell");
  assert.equal(surface.opacity, "1", "The menu is opaque after its entrance animation finishes");
  assert.ok(
    surface.background.startsWith("rgb(") &&
      surface.background
        .match(/\d+/g)
        .slice(0, 3)
        .every((channel) => Number(channel) < 100),
    `Portalled controls inherit the dark surface: ${surface.background}`,
  );
}

async function settleAnimations(locator) {
  await locator.evaluate(async (element) => {
    await Promise.all(
      element
        .getAnimations({ subtree: true })
        .filter((animation) => Number.isFinite(animation.effect?.getTiming().iterations))
        .map((animation) => animation.finished.catch(() => {})),
    );
  });
}

/** Use isolated contexts so theme preferences cannot alter the main portal fixture journey. */
export async function assertPartnerThemeJourneys({ browser, context, origin, root }) {
  const storageState = await context.storageState();
  const contexts = [];
  const errors = [];
  let active;
  async function createContext(options = {}) {
    const current = await browser.newContext({
      storageState,
      viewport: { width: 1440, height: 900 },
      colorScheme: "light",
      ...options,
    });
    contexts.push(current);
    current.on("page", (page) => {
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error" && /hydrat|did not match/i.test(message.text()))
          errors.push(message.text());
      });
    });
    return current;
  }
  try {
    const prepaint = await createContext({
      storageState: {
        cookies: storageState.cookies,
        origins: [{ origin, localStorage: [{ name: key, value: "dark" }] }],
      },
    });
    let blockedScripts = 0;
    await prepaint.route("**/*", async (route) => {
      if (route.request().resourceType() === "script") {
        blockedScripts++;
        await route.abort();
      } else await route.continue();
    });
    active = await prepaint.newPage();
    await active.goto(origin, { waitUntil: "domcontentloaded" });
    await active.getByRole("heading", { name: "Dashboard", exact: true, level: 1 }).waitFor();
    assert.ok(blockedScripts > 0, "Hydration scripts were prevented from running");
    assert.equal(
      await active.evaluate(() => document.documentElement.classList.contains("dark")),
      true,
    );
    assert.equal(
      await active.locator(".theme-toggle").getAttribute("aria-label"),
      label("system"),
      "The unhydrated control still has server state while the head bootstrap applies saved dark",
    );
    await active.screenshot({ path: root + "reports/partner/theme-prepaint-dark.png" });
    await prepaint.close();

    const themes = await createContext();
    active = await themes.newPage();
    await active.goto(origin);
    await expectTheme(active, "system", false);
    const toggle = active.locator(".theme-toggle");
    await toggle.focus();
    await active.keyboard.press("Enter");
    await expectTheme(active, "light", false);
    await active.keyboard.press("Space");
    await expectTheme(active, "dark", true);
    assert.equal(
      await active.evaluate((storageKey) => localStorage.getItem(storageKey), key),
      "dark",
    );
    await active.reload();
    await expectTheme(active, "dark", true);
    await active
      .getByRole("navigation", { name: "Partner portal", exact: true })
      .getByRole("link", { name: "Products", exact: true })
      .click();
    await active.waitForURL((url) => url.pathname === "/products");
    await expectTheme(active, "dark", true);

    const second = await themes.newPage();
    await second.goto(origin);
    await expectTheme(second, "dark", true);
    await toggle.click();
    await expectTheme(active, "system", false);
    await expectTheme(second, "system", false);
    await active.emulateMedia({ colorScheme: "dark" });
    await expectTheme(active, "system", true);
    await toggle.click();
    await expectTheme(active, "light", false);
    await active.emulateMedia({ colorScheme: "light" });
    await active.emulateMedia({ colorScheme: "dark" });
    await expectTheme(active, "light", false);
    await expectTheme(second, "light", false);
    await second.locator(".theme-toggle").click();
    await expectTheme(active, "dark", true);
    await expectTheme(second, "dark", true);
    await second.evaluate(() => localStorage.clear());
    await expectTheme(active, "system", true);
    await toggle.click();
    await toggle.click();
    await expectTheme(active, "dark", true);
    await second.close();

    await active.goto(origin);
    await expectTheme(active, "dark", true);
    await active.getByRole("figure", { name: "Impressions and clicks", exact: true }).waitFor();
    await active.getByRole("button", { name: "User menu: Alex Partner", exact: true }).click();
    await assertDarkPortal(active.getByRole("menu"));
    await active.screenshot({ path: root + "reports/partner/theme-dark-desktop.png" });
    await active.keyboard.press("Escape");
    await active.getByRole("combobox", { name: "Reporting period", exact: true }).click();
    await assertDarkPortal(active.locator(".shared-select-popover"));
    await active.keyboard.press("Escape");

    for (const width of [320, 375]) {
      await active.setViewportSize({ width, height: 844 });
      const header = await active.locator(".partner-workspace > header").evaluate((element) => {
        const brand = element.querySelector("a").getBoundingClientRect();
        const controls = [...element.querySelectorAll("button")].map((button) => {
          const { x, y, width, height } = button.getBoundingClientRect();
          return { x, y, width, height };
        });
        return {
          brandRight: brand.right,
          controls,
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      assert.equal(header.overflow, false, `The ${width}px portal has no horizontal overflow`);
      assert.equal(header.controls.length, 2);
      assert.ok(
        header.controls[0].x >= header.brandRight,
        "Theme control does not overlap the brand",
      );
      assert.ok(header.controls.every((box) => box.x >= 0 && box.x + box.width <= width));
      assert.ok(
        Math.abs(header.controls[0].y - header.controls[1].y) < 5,
        "Header controls share one row",
      );
      await active.screenshot({ path: root + `reports/partner/theme-dark-${width}.png` });
    }
    await themes.clearCookies();
    await active.goto(origin + "/login");
    await active.getByRole("heading", { name: "Sign in to DevFeed Partners" }).waitFor();
    await settleAnimations(active.locator(".partner-login"));
    assert.equal(
      await active
        .locator(".partner-login")
        .evaluate((element) => getComputedStyle(element).colorScheme),
      "light",
    );
    await active.screenshot({ path: root + "reports/partner/theme-dark-login.png" });

    const unavailable = await createContext({ colorScheme: "dark" });
    await unavailable.addInitScript(() => {
      for (const method of ["getItem", "setItem"]) {
        Storage.prototype[method] = () => {
          throw new DOMException("Storage unavailable", "SecurityError");
        };
      }
    });
    active = await unavailable.newPage();
    await active.goto(origin);
    await expectTheme(active, "system", true);
    await active.locator(".theme-toggle").click();
    await expectTheme(active, "light", false);
    await active.locator(".theme-toggle").click();
    await expectTheme(active, "dark", true);
    assert.deepEqual(errors, [], "Theme scenarios have no runtime or hydration errors");
    console.log(
      "Partner theme prepaint, keyboard/system modes, persistence, cross-tab sync, denied storage, dark portals and responsive header passed.",
    );
  } catch (error) {
    if (active && !active.isClosed())
      await active.screenshot({ path: root + "reports/partner/theme-failure.png", fullPage: true });
    throw error;
  } finally {
    await Promise.all(contexts.map((current) => current.close()));
  }
}

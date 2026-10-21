import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";

export function readerRouteUrl(currentUrl, route) {
  const current = new URL(currentUrl);
  if (current.protocol === "chrome-extension:" || current.hash.startsWith("#/")) {
    current.hash = route;
    return current.href;
  }
  return new URL(route, current).href;
}

export async function openArticleViewSettings(page) {
  const destination = readerRouteUrl(page.url(), "/settings/appearance");
  if (page.url() !== destination) await page.goto(destination);
  const region = page.getByRole("region", { name: "Article feed layout", exact: true });
  await region.waitFor();
  await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
  return region;
}

export async function chooseArticleView(page, view, { keyboard = false } = {}) {
  const name = {
    cards: "Grid",
    compact: "List",
    stacked: "Stacked",
    Grid: "Grid",
    List: "List",
    Stacked: "Stacked",
  }[view];
  assert.ok(name, `Unknown article layout: ${view}`);
  const region = page.getByRole("region", { name: "Article feed layout", exact: true });
  const radio = region.getByRole("radio", { name, exact: true });
  await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
  if (keyboard) {
    await radio.focus();
    await page.keyboard.press("Space");
  } else await radio.check();
  await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
  assert.equal(await radio.isChecked(), true, `${name} is saved in Appearance`);
  assert.equal(await region.getByRole("alert").count(), 0, "The layout save succeeds");
  return region;
}

/** Save through the same Appearance controls in the website and both extensions. */
export async function setArticleView(page, view, { returnTo = page.url(), keyboard = false } = {}) {
  await openArticleViewSettings(page);
  await chooseArticleView(page, view, { keyboard });
  if (page.url() !== returnTo) await page.goto(returnTo);
  assert.equal(page.url(), returnTo, "Appearance returns to the exact reader route and filters");
}

export async function checkArticleViewSettings(
  page,
  target,
  output,
  { signedIn = false, routes = [] } = {},
) {
  const viewport = page.viewportSize();
  await page.goto(target);
  await setArticleView(page, "cards", { returnTo: target });
  await page.locator(".article-card:visible").first().waitFor();
  const article = await page.locator(".card-open-link:visible").first().getAttribute("href");
  let originalBookmark;
  if (signedIn) {
    const bookmark = page.locator(".article-card:visible .bookmark-button[aria-pressed]").first();
    await bookmark.waitFor();
    originalBookmark = await bookmark.getAttribute("aria-pressed");
    if (originalBookmark === "false") {
      await bookmark.click();
      await page
        .locator('.article-card:visible .bookmark-button[aria-pressed="true"]')
        .first()
        .waitFor();
    }
  }
  assert.equal(page.url(), target, "The filtered feed is settled before opening Appearance");
  await page.setViewportSize({ width: 320, height: 1000 });
  const header = page.getByRole("banner");
  assert.equal(
    await header.locator('a[href$="/settings/appearance"]').count(),
    0,
    "Appearance has no standalone header link",
  );
  const avatar = header.getByRole("button", { name: /^User menu:/ });
  if (signedIn) {
    for (const width of [1440, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await avatar.waitFor();
      await avatar.focus();
      await page.keyboard.press("ArrowDown");
      const menu = page.getByRole("menu");
      await menu.waitFor();
      assert.equal(
        await menu.getByRole("menuitem", { name: /^(Claim your username|Your Dev Card)$/ }).count(),
        1,
        `The ${width}px avatar menu has exactly one profile editor action`,
      );
      assert.equal(
        await menu.getByRole("menuitem", { name: "Claim your username", exact: true }).count(),
        1,
        "An unclaimed reader uses the username action",
      );
      for (const name of ["Leaderboard", "Notifications"])
        assert.equal(
          await menu.getByRole("menuitem", { name, exact: true, includeHidden: true }).count(),
          0,
          `${name} is omitted from the ${width}px avatar menu`,
        );
      if (output) {
        await mkdir(dirname(output), { recursive: true });
        await page.screenshot({
          path: `${output}-account-menu-${width}.png`,
          animations: "disabled",
        });
      }
      if (width === 1440) {
        await page.keyboard.press("Escape");
        await menu.waitFor({ state: "hidden" });
        continue;
      }
      const appearance = menu.getByRole("menuitem", { name: "Appearance", exact: true });
      await appearance.waitFor();
      await page.waitForFunction(() => document.activeElement?.getAttribute("role") === "menuitem");
      const items = await menu.getByRole("menuitem").count();
      for (let index = 0; index < items; index++) {
        if (await appearance.evaluate((node) => node === document.activeElement)) break;
        const focusedItem = await menu
          .getByRole("menuitem")
          .evaluateAll((nodes) => nodes.indexOf(document.activeElement));
        await page.keyboard.press("ArrowDown");
        await page.waitForFunction(
          (previous) =>
            Array.from(document.querySelectorAll('[role="menuitem"]')).indexOf(
              document.activeElement,
            ) !== previous,
          focusedItem,
        );
      }
      assert.equal(
        await appearance.evaluate((node) => node === document.activeElement),
        true,
        "Appearance is reachable with the keyboard in the mobile avatar menu",
      );
      await page.keyboard.press("Enter");
    }
  } else {
    assert.equal(await avatar.count(), 0, "Guests have no avatar menu");
    await page.goto(readerRouteUrl(target, "/settings/appearance"));
  }
  await page.waitForURL(readerRouteUrl(target, "/settings/appearance"));
  const region = await openArticleViewSettings(page);
  const grid = region.getByRole("radio", { name: "Grid", exact: true });
  const list = region.getByRole("radio", { name: "List", exact: true });
  const savedNotice = page.getByText("Article layout saved.", { exact: true });
  assert.equal(await grid.isChecked(), true);
  assert.equal(await region.getByRole("radio").count(), 3);
  assert.equal(await page.getByRole("radio", { name: /^(Cards|Compact list)$/ }).count(), 0);
  assert.equal(await page.getByText("Changes save automatically.", { exact: true }).count(), 0);
  assert.equal(await savedNotice.isVisible(), false, "Loading a saved layout has no success toast");
  if (signedIn) {
    let release;
    let requested;
    let payload;
    const barrier = new Promise((resolve) => {
      release = resolve;
    });
    const pending = new Promise((resolve) => {
      requested = resolve;
    });
    const pattern = /\/api\/v1\/user\/settings\/feed(?:\?|$)/;
    const holdSave = async (route) => {
      if (route.request().method() !== "PUT") return route.fallback();
      payload = route.request().postDataJSON();
      requested();
      await barrier;
      return route.fallback();
    };
    await page.route(pattern, holdSave);
    let timer;
    try {
      await list.focus();
      await page.keyboard.press("Space");
      await Promise.race([
        pending,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("Appearance did not save the layout")), 15000);
        }),
      ]);
      assert.equal(await region.getAttribute("aria-busy"), "true");
      assert.equal(
        await savedNotice.isVisible(),
        false,
        "A pending layout save has no success toast",
      );
      assert.equal(await region.getByText("Saving…", { exact: true }).count(), 0);
      assert.equal(await grid.isDisabled(), true, "Another layout cannot race the pending save");
      assert.equal(await list.isDisabled(), false, "The selected layout retains keyboard focus");
      assert.equal(await list.evaluate((node) => node === document.activeElement), true);
      assert.equal(payload.view, "compact", "The saved API value remains backward compatible");
    } finally {
      clearTimeout(timer);
      release();
      await page.locator('[aria-label="Article feed layout"][aria-busy="false"]').waitFor();
      await page.unroute(pattern, holdSave);
    }
  } else {
    await chooseArticleView(page, "compact", { keyboard: true });
    assert.equal(
      await page.evaluate(() => localStorage.getItem("devfeed:feed-view:guest")),
      "compact",
      "Guest Appearance preferences persist without signing in",
    );
  }
  assert.equal(await list.isChecked(), true);
  await savedNotice.waitFor();
  const noticeBounds = await page.locator('.settings-toast[data-variant="success"]').boundingBox();
  assert.ok(
    noticeBounds &&
      noticeBounds.x >= -1 &&
      noticeBounds.x + noticeBounds.width <= 321 &&
      noticeBounds.y >= -1 &&
      noticeBounds.y + noticeBounds.height <= 1001,
    "The layout success toast fits the 320px mobile viewport",
  );
  if (output) {
    await mkdir(dirname(output), { recursive: true });
    await page.screenshot({ path: `${output}-saved-toast.png` });
  }
  const dismissNotice = page.getByRole("button", { name: "Dismiss notification", exact: true });
  await dismissNotice.waitFor();
  await dismissNotice.click();
  await savedNotice.waitFor({ state: "hidden" });
  if (output) {
    await mkdir(dirname(output), { recursive: true });
  }
  for (const width of [320, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
      `Appearance fits the ${width}px viewport without horizontal scrolling`,
    );
    const choices = await region.locator(".feed-layout-option").evaluateAll((labels) =>
      labels.map((label) => {
        const box = label.getBoundingClientRect();
        return { width: box.width, height: box.height, right: box.right };
      }),
    );
    for (const choice of choices)
      assert.ok(choice.width >= 44 && choice.height >= 44 && choice.right <= width + 1);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (output)
      await page.screenshot({
        path: `${output}-appearance-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
  }
  if (viewport) await page.setViewportSize(viewport);
  await page.goBack();
  await page.waitForURL(target);
  await page.locator(".article-list-row:visible").first().waitFor();
  assert.equal(page.url(), target, "Browser Back restores the exact filtered feed route");
  await page.goForward();
  await openArticleViewSettings(page);
  await page.reload();
  await openArticleViewSettings(page);
  assert.equal(await list.isChecked(), true, "Appearance reload restores the saved layout");
  for (const route of [target, ...routes]) {
    await page.goto(route);
    try {
      await page.locator(".article-list-row:visible").first().waitFor();
    } catch (error) {
      if (output) await page.screenshot({ path: `${output}-failure.png`, animations: "disabled" });
      error.message = `${route}: ${error.message}`;
      throw error;
    }
    assert.equal(page.url(), route, "Layout changes preserve the requested route and filters");
    assert.equal(await page.locator(".feed-view-toggle").count(), 0);
  }
  await page.goto(target);
  const title = page.locator(".article-list-title:visible").first();
  await title.waitFor();
  assert.equal(await title.getAttribute("href"), article, "The same article survives Settings");
  if (signedIn)
    await page
      .locator('.article-list-row:visible .bookmark-button[aria-pressed="true"]')
      .first()
      .waitFor();
  const row = await page.locator(".article-list-row:visible").first().elementHandle();
  try {
    await title.click();
    await page.getByRole("dialog", { name: "Article preview", exact: true }).waitFor();
    await page.getByRole("button", { name: "Close preview", exact: true }).click();
    await page
      .getByRole("dialog", { name: "Article preview", exact: true })
      .waitFor({ state: "hidden" });
    await page.waitForURL(target);
    assert.equal(page.url(), target, "Closing an article restores the exact filtered route");
    assert.equal(await row.evaluate((node) => node.isConnected), true);
  } finally {
    await row.dispose();
  }
  if (output) await page.screenshot({ path: `${output}-list.png`, animations: "disabled" });
  if (signedIn) {
    await page.goto(readerRouteUrl(target, "/settings/feed"));
    const feed = page.getByRole("region", { name: "Feed preferences", exact: true });
    await feed.waitFor();
    assert.equal(await feed.getByRole("radio").count(), 0);
    await feed
      .getByRole("link", { name: "Change article layout in Appearance.", exact: true })
      .click();
    await openArticleViewSettings(page);
    assert.equal(await list.isChecked(), true);
  }
  await setArticleView(page, "cards", { returnTo: target });
  await page.locator(".article-card:visible").first().waitFor();
  if (originalBookmark === "false") {
    await page
      .locator('.article-card:visible .bookmark-button[aria-pressed="true"]')
      .first()
      .click();
    await page
      .locator('.article-card:visible .bookmark-button[aria-pressed="false"]')
      .first()
      .waitFor();
  }
  await page.reload();
  await page.locator(".article-card:visible").first().waitFor();
  assert.equal(await page.locator(".article-list-row:visible").count(), 0);
  if (signedIn) {
    const profilePattern = /\/api\/v1\/user\/settings\/profile(?:\?|$)/;
    const claimedProfile = (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return route.fulfill({
        json: {
          display_name: "Claimed Reader",
          avatar_url: null,
          username: "claimed-reader",
          visibility: { public: true },
        },
      });
    };
    await page.route(profilePattern, claimedProfile);
    try {
      await page.reload();
      for (const width of [1440, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        const claimedAvatar = page.getByRole("button", { name: /^User menu:.*Claimed Reader$/ });
        await claimedAvatar.waitFor();
        await claimedAvatar.click();
        const menu = page.getByRole("menu");
        await menu.waitFor();
        assert.equal(
          await menu
            .getByRole("menuitem", { name: /^(Claim your username|Your Dev Card)$/ })
            .count(),
          1,
          `The claimed reader has exactly one profile editor action at ${width}px`,
        );
        assert.equal(
          await menu.getByRole("menuitem", { name: "Your Dev Card", exact: true }).count(),
          1,
        );
        assert.equal(
          await menu.getByRole("menuitem", { name: "Your Profile", exact: true }).count(),
          1,
        );
        if (output)
          await page.screenshot({
            path: `${output}-claimed-account-menu-${width}.png`,
            animations: "disabled",
          });
        await page.keyboard.press("Escape");
        await menu.waitFor({ state: "hidden" });
      }
    } finally {
      await page.unroute(profilePattern, claimedProfile);
      if (viewport) await page.setViewportSize(viewport);
      await page.reload();
      await avatar.waitFor();
    }
  }
}

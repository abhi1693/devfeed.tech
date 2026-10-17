import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

export const keyboardProfile = {
  username: "keyboard-reader",
  display_name: "Keyboard Reader",
  avatar_url: null,
  bio: "A public reading calendar.",
  links: [],
  stack: [],
  reading_streak: null,
};
export const keyboardActivity = {
  year: 2026,
  timezone: "UTC",
  days: Array.from({ length: 365 }, (_, index) => ({
    date: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
    article_count: index % 5,
  })),
};

async function tabTo(page, locator, limit = 80) {
  for (let index = 0; index < limit; index++) {
    if (await locator.evaluate((node) => document.activeElement === node)) return;
    await page.keyboard.press("Tab");
  }
  assert.fail("Keyboard focus did not reach the expected control");
}

/** Use real keyboard activation and scrolling in the built website and extensions. */
export async function checkKeyboardReader(page, destination, output) {
  await mkdir(output, { recursive: true });
  await page.goto(destination("/mcp"));
  const config = page.getByLabel("MCP configuration", { exact: true });
  await config.waitFor();
  assert.equal(await config.getAttribute("tabindex"), null);
  assert.equal(await config.evaluate((node) => node.scrollWidth <= node.clientWidth), true);
  await page.evaluate(() => {
    window.__keyboardCopied = null;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text) => {
          window.__keyboardCopied = text;
        },
      },
    });
  });
  await page.getByLabel("MCP server URL", { exact: true }).focus();
  const copy = page.getByRole("button", { name: "Copy configuration", exact: true });
  await tabTo(page, copy);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.__keyboardCopied !== null);
  assert.equal(await page.evaluate(() => window.__keyboardCopied), await config.textContent());
  await page.keyboard.press("Tab");
  assert.equal(await config.evaluate((node) => document.activeElement === node), false);
  await page.screenshot({ path: `${output}/mcp-keyboard.png`, animations: "disabled" });

  await page.goto(destination("/latest"));
  await page.locator(".article-card").first().waitFor();
  await page.locator(".card-open-link").first().click();
  const preview = page.getByRole("dialog", { name: "Article preview", exact: true });
  const original = preview.getByRole("link", {
    name: /^Read (article|tutorial|news)/,
  });
  await original.waitFor();
  const publisherUrl = await original.getAttribute("href");
  let opens = 0;
  await page.route("**/api/v1/user/articles/*/open", (route) => {
    opens++;
    assert.equal(route.request().method(), "POST");
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    return route.fulfill({
      json: { article_id: id, opens, likes: 0, liked: false, bookmarked: false },
    });
  });
  await page.context().route(publisherUrl, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<h1>Original publisher</h1>",
    }),
  );
  await preview.getByRole("button", { name: "Close preview", exact: true }).focus();
  await tabTo(page, original);
  const openRecorded = page.waitForResponse(
    (response) =>
      /\/api\/v1\/user\/articles\/[^/]+\/open$/.test(new URL(response.url()).pathname) &&
      response.request().method() === "POST" &&
      response.ok(),
  );
  const popupReady = page.context().waitForEvent("page");
  await page.keyboard.press("Enter");
  const popup = await popupReady;
  await popup.getByRole("heading", { name: "Original publisher" }).waitFor();
  await openRecorded;
  await popup.close();
  assert.equal(opens, 1, "Native keyboard link activation records one original open");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(destination("/users/keyboard-reader"));
  await page.getByRole("heading", { name: keyboardProfile.display_name, exact: true }).waitFor();
  const calendar = page.getByRole("region", { name: "Reading activity for 2026", exact: true });
  assert.equal(await calendar.getAttribute("tabindex"), "0");
  // Visibility revalidation can replace the server-rendered calendar during hydration.
  const overflowingCalendar = await page.waitForFunction(() => {
    const current = document.querySelector(".public-profile-calendar-scroll");
    return Boolean(
      current?.isConnected && current.clientWidth > 0 && current.scrollWidth > current.clientWidth,
    );
  });
  assert.equal(await overflowingCalendar.jsonValue(), true);
  await page.getByRole("link").first().focus();
  await tabTo(page, calendar);
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(
    () => document.querySelector(".public-profile-calendar-scroll").scrollLeft > 0,
  );
  await page.screenshot({ path: `${output}/calendar-keyboard.png`, animations: "disabled" });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}

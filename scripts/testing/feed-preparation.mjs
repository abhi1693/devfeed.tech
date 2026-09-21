import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

/** The same preparation/recovery journey runs in the website, Chrome, and Edge. */
export async function checkFeedPreparation(page, home, article, output) {
  await mkdir(output, { recursive: true });
  const pattern = "**/api/v1/user/feed?**";
  let kind = "following";
  let phase = "preparing";
  await page.route(pattern, async (route) => {
    if (phase === "error") return route.fulfill({ status: 503, json: {} });
    const ready = phase === "ready";
    return route.fulfill({
      json: {
        status: ready ? "ready" : "refreshing",
        refresh_state: ready ? "idle" : "retrying",
        feed_kind: ready ? "personalized" : kind,
        generation: ready ? "prepared-generation" : null,
        has_interests: true,
        items: [
          {
            ...article,
            title: ready ? "Your personalized result" : "Read this while we prepare your feed",
          },
        ],
        next_cursor: null,
        reasons: {},
      },
    });
  });
  const checkBannerSpacing = async () => {
    const toolbar = await page.locator(".feed-toolbar").boundingBox();
    const banner = await page.getByRole("region", { name: "Feed preparation" }).boundingBox();
    assert.ok(toolbar && banner, "Feed toolbar and preparation banner must be visible");
    assert.ok(
      banner.y - (toolbar.y + toolbar.height) >= 20,
      "Banner needs space below the divider",
    );
  };
  try {
    await page.bringToFront();
    await page.goto(home);
    const title = page.getByRole("link", {
      name: "Read this while we prepare your feed",
      exact: true,
    });
    await title.waitFor();
    await page.getByRole("heading", { name: "Recent articles from your follows" }).waitFor();
    await page
      .getByText("Your recommendations are taking longer than usual", { exact: true })
      .waitFor();
    const card = await page.locator(".article-card").first().elementHandle();
    await checkBannerSpacing();
    await page.screenshot({ path: `${output}/preparing-desktop.png`, animations: "disabled" });
    phase = "error";
    await page.getByText("We couldn’t check your recommendations", { exact: true }).waitFor();
    assert.equal(await card.evaluate((node) => node.isConnected), true);
    phase = "ready";
    await page.getByRole("button", { name: "Check again", exact: true }).click();
    await page.getByText("Your feed is ready", { exact: true }).waitFor();
    assert.equal(await card.evaluate((node) => node.isConnected), true);
    assert.equal(
      await page.getByRole("link", { name: "Your personalized result", exact: true }).count(),
      0,
    );
    await checkBannerSpacing();
    await page.screenshot({ path: `${output}/ready-desktop.png`, animations: "disabled" });
    await page.getByRole("button", { name: "Show updates", exact: true }).click();
    await page.getByRole("link", { name: "Your personalized result", exact: true }).waitFor();
    assert.equal(await page.getByRole("region", { name: "Feed preparation" }).count(), 0);
    await card.dispose();

    kind = "latest";
    phase = "preparing";
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await title.waitFor();
    await page
      .getByRole("heading", { name: "Latest articles while we prepare your feed" })
      .waitFor();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      true,
    );
    await checkBannerSpacing();
    await page.screenshot({ path: `${output}/preparing-mobile.png`, animations: "disabled" });
    await page
      .getByRole("region", { name: "Feed preparation" })
      .getByRole("link", { name: "Browse latest articles" })
      .click();
    await page.waitForURL(/(?:#)?\/latest$/);
  } catch (error) {
    await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
    console.error("Feed preparation failure", page.url(), await page.locator("main").innerText());
    throw error;
  } finally {
    await page.unroute(pattern);
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
}

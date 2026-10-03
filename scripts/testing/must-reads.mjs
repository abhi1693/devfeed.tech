import assert from "node:assert/strict";

export function dailyFixture(article) {
  return {
    enabled: false,
    presented: false,
    holdClaim: null,
    onClaim: null,
    claims: 0,
    async response(presentation = false) {
      if (presentation) {
        this.claims++;
        const claimed = !this.presented;
        this.presented = true;
        this.onClaim?.();
        await this.holdClaim;
        return { claimed };
      }
      return {
        date: new Date().toLocaleDateString("en-CA"),
        timezone: "UTC",
        items: this.enabled
          ? Array.from({ length: 5 }, (_, index) => ({
              ...article,
              id: `33333333-3333-4333-8333-33333333333${index}`,
              title: `Daily Must Read ${index + 1}`,
            }))
          : [],
        reasons: Object.fromEntries(
          Array.from({ length: 5 }, (_, index) => [
            `33333333-3333-4333-8333-33333333333${index}`,
            "Because you follow software development",
          ]),
        ),
        read_ids: [],
        preparing: false,
        presented: this.presented,
      };
    },
  };
}

export async function checkMustReads(page, fixture, screenshot) {
  await page.bringToFront();
  let releaseClaim;
  fixture.holdClaim = new Promise((resolve) => {
    releaseClaim = resolve;
  });
  const claimStarted = new Promise((resolve) => {
    fixture.onClaim = resolve;
  });
  fixture.enabled = true;
  const response = page.waitForResponse((value) =>
    new URL(value.url()).pathname.endsWith("/user/must-reads"),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await response;
  const modal = page.getByRole("dialog", { name: "Today’s Must Reads", exact: true });
  await claimStarted;
  await page.evaluate(() => {
    const blocker = document.createElement("dialog");
    blocker.id = "must-reads-race-blocker";
    blocker.textContent = "Another reader dialog";
    document.body.append(blocker);
    blocker.showModal();
  });
  const claimResponse = page.waitForResponse((value) =>
    new URL(value.url()).pathname.endsWith("/must-reads/presentation"),
  );
  releaseClaim();
  await claimResponse;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(3000);
  assert.equal(await modal.count(), 0, "a successful in-flight claim waits for the other dialog");
  assert.equal(fixture.claims, 1, "does not claim again while pending");
  await page.evaluate(() => document.getElementById("must-reads-race-blocker").remove());
  await modal.waitFor({ timeout: 15000 });
  fixture.holdClaim = null;
  fixture.onClaim = null;
  assert.equal(
    await modal.getByText("Your interests. A daily selection worth your time.").count(),
    0,
  );
  assert.equal(await modal.locator(".article-card").count(), 5);
  assert.equal(await modal.getByRole("heading", { name: "Your daily briefing" }).count(), 1);
  const desktopViewport = page.viewportSize();
  for (const viewport of [
    { width: 1144, height: 909 },
    { width: 1366, height: 768 },
    { width: 1440, height: 900 },
  ]) {
    await page.setViewportSize(viewport);
    const layout = await modal.evaluate((node) => {
      const bounds = node.getBoundingClientRect();
      return {
        scrolls: node.scrollHeight > node.clientHeight + 1,
        top: bounds.top,
        bottom: bounds.bottom,
        cards: [...node.querySelectorAll(".article-card")].map((card) => ({
          bottom: card.getBoundingClientRect().bottom,
          footer: card.querySelector(".card-bottom").getBoundingClientRect().bottom,
          imageHeight: card.querySelector(".card-image").getBoundingClientRect().height,
        })),
      };
    });
    assert.equal(
      layout.scrolls,
      false,
      `modal fits without scrolling at ${viewport.width}x${viewport.height}`,
    );
    assert.ok(layout.top >= 0 && layout.bottom <= viewport.height);
    for (const card of layout.cards) {
      assert.ok(card.footer <= card.bottom + 1, "card actions remain visible");
      assert.ok(card.imageHeight > 0, "card image remains visible");
    }
  }
  await page.setViewportSize(desktopViewport);
  await modal.screenshot({ path: `${screenshot}-desktop.png` });
  const dark = await page.locator("html").evaluate((node) => node.classList.contains("dark"));
  await page.locator("html").evaluate((node) => node.classList.toggle("dark"));
  await modal.screenshot({ path: `${screenshot}-alternate-theme.png` });
  await page.locator("html").evaluate((node, value) => node.classList.toggle("dark", value), dark);
  await modal.getByRole("button", { name: "Save unread picks", exact: true }).click();
  await modal.getByRole("button", { name: "Unread picks saved" }).waitFor();
  await page.keyboard.press("Escape");
  await modal.waitFor({ state: "hidden" });
  const trigger = page.getByRole("button", { name: "Today’s Must Reads", exact: true });
  await trigger.focus();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(3000);
  assert.equal(await modal.count(), 0, "does not auto-open twice");
  const original = page.viewportSize();
  await page.setViewportSize({ width: 375, height: 844 });
  await trigger.click();
  await modal.waitFor();
  const bounds = await modal.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 375);
  await modal.screenshot({ path: `${screenshot}-mobile.png` });
  await modal.getByRole("button", { name: "Close Must Reads" }).click();
  await page.setViewportSize(original);
  await trigger.click();
  await modal.waitFor();
  await modal.getByRole("link", { name: "Daily Must Read 1", exact: true }).click();
  await modal.waitFor({ state: "hidden" });
  await page.waitForURL(
    (url) => url.pathname.startsWith("/articles/") || url.hash.startsWith("#/articles/"),
  );
  fixture.enabled = false;
  await page.goBack();
}

import assert from "node:assert/strict";

export async function checkDevCardPromo(page, screenshotPrefix, { extension = false } = {}) {
  const promo = page.getByRole("region", { name: "Discover your dev card" });
  await promo.waitFor();
  const dialog = page.getByRole("dialog", { name: "Your dev card preview" });
  assert.equal(await dialog.evaluate((node) => node.matches(":modal")), true);
  assert.equal(
    await page.locator(".article-grid [aria-label='Discover your dev card']").count(),
    0,
  );
  assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden");
  for (let index = 0; index < 6; index++) {
    await page.keyboard.press(index < 3 ? "Tab" : "Shift+Tab");
    assert.equal(await dialog.evaluate((node) => node.contains(document.activeElement)), true);
  }

  const authPath = "**/api/v1/user/auth/me";
  const authUnavailable = (route) =>
    route.fulfill({ status: 503, json: { detail: "User authentication is not configured" } });
  await page.context().route(authPath, authUnavailable);
  try {
    await page.reload();
    await promo.getByRole("button", { name: "Create your dev card" }).click();
    await promo.getByLabel("Your display name").fill("Offline preview");
    assert.equal(await promo.getByRole("button", { name: "Save my dev card" }).isDisabled(), true);
    assert.equal(await promo.getByRole("link", { name: "Save my dev card" }).count(), 0);
    await dialog.getByRole("button", { name: "Dismiss dev card preview" }).click();
    await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-promo-dismissed"));
  } finally {
    await page.context().unroute(authPath, authUnavailable);
  }
  await page.emulateMedia({ reducedMotion: "no-preference" });
  // A fresh navigation proves the reveal starts on entry, rather than at bundle load.
  await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-promo-revealed"));
  await page.reload();
  await dialog.waitFor();
  assert.equal(await dialog.getAttribute("data-details"), "false", "show only the card first");
  assert.equal(
    await dialog.getByRole("button", { name: "Create your dev card" }).count(),
    0,
    "details are inaccessible during the reveal",
  );
  assert.equal(
    await dialog.evaluate((node) => getComputedStyle(node).backgroundColor),
    "rgba(0, 0, 0, 0)",
    "no modal panel before the card reveal",
  );
  const revealWidth = (await dialog.boundingBox()).width;
  await page.screenshot({ path: `${screenshotPrefix}-card-first.png` });
  await page.waitForFunction(
    () =>
      document.querySelector('dialog[aria-label="Your dev card preview"]')?.dataset.details ===
      "true",
  );
  await page.waitForTimeout(700);
  assert.ok(
    (await dialog.boundingBox()).width > revealWidth * 1.5,
    "the right panel expands after the reveal",
  );
  await promo.scrollIntoViewIfNeeded();
  await page.waitForFunction(
    () =>
      document.querySelector('[aria-label="Discover your dev card"]')?.dataset.revealed === "true",
  );
  const reveal = promo.locator('[data-play="true"]');
  await reveal.waitFor();
  const frames = await reveal.evaluate((node) => {
    const animation = node.getAnimations()[0];
    animation.pause();
    animation.currentTime = 100;
    const smallWidth = node.getBoundingClientRect().width;
    animation.currentTime = 1800;
    const fullWidth = node.getBoundingClientRect().width;
    animation.currentTime = 450;
    return { transform: getComputedStyle(node).transform, smallWidth, fullWidth };
  });
  assert.match(frames.transform, /^matrix3d\(/, "the reveal must rotate in 3D");
  assert.ok(frames.fullWidth > frames.smallWidth * 2, "the card visibly zooms in as it rotates");
  await page.screenshot({ path: `${screenshotPrefix}-reveal.png` });
  await reveal.evaluate((node) => node.getAnimations()[0].finish());
  await page.waitForTimeout(1400);
  await page.screenshot({ path: `${screenshotPrefix}-desktop.png` });
  const theme = await page.locator("html").getAttribute("class");
  await page.locator("html").evaluate((node) => node.classList.add("dark"));
  await page.screenshot({ path: `${screenshotPrefix}-dark.png`, animations: "disabled" });
  await page.locator("html").evaluate((node, value) => {
    node.className = value ?? "";
  }, theme);
  const stage = promo.getByTestId("dev-card-stage");
  const box = await stage.boundingBox();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.4);
  assert.ok(await stage.locator('[style*="--tilt-y"]').count(), "mouse movement tilts the card");
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(
    await promo.evaluate(
      (node) =>
        node
          .getAnimations({ subtree: true })
          .filter((animation) => animation.playState === "running").length,
    ),
    0,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await promo.scrollIntoViewIfNeeded();
  assert.ok(await promo.evaluate((node) => node.getBoundingClientRect().right <= innerWidth));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: `${screenshotPrefix}-mobile.png`, fullPage: true });
  await promo.getByRole("button", { name: "Create your dev card" }).click();
  const name = promo.getByLabel("Your display name");
  assert.equal(await name.evaluate((node) => node === document.activeElement), true);
  await name.fill("Maya Chen");
  await promo.getByLabel("Your technologies").fill("TypeScript");
  await promo.getByRole("button", { name: "Add TypeScript", exact: true }).click();
  assert.ok(await promo.getByRole("img", { name: "Dev card for Maya Chen" }).count());
  assert.equal(await promo.getByText("DAY STREAK", { exact: true }).count(), 0);
  const signup = promo.getByRole("link", { name: "Save my dev card" });
  const url = new URL(await signup.getAttribute("href"), "https://devfeed.tech");
  assert.equal(url.searchParams.get("register"), "true");
  assert.equal(
    url.searchParams.get("return_to"),
    extension ? "/extension/login-complete" : "/settings/profile",
  );
  // Keep this tab on the preview while checking the same click's draft persistence.
  await signup.evaluate((node) =>
    node.addEventListener("click", (event) => event.preventDefault(), { once: true }),
  );
  await signup.click();
  const draft = await page.evaluate(() =>
    JSON.parse(sessionStorage.getItem("devfeed:dev-card-draft")),
  );
  assert.equal(draft.name, "Maya Chen");
  assert.equal(draft.stack[0].name, "TypeScript");
  assert.equal(draft.ready, true);
  await page.evaluate(() => {
    document.activeElement?.blur();
    scrollTo(0, 0);
  });
  await page.screenshot({ path: `${screenshotPrefix}-personalized.png`, fullPage: true });
  await page.setViewportSize({ width: 320, height: 740 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.reload();
  await promo.scrollIntoViewIfNeeded();
  assert.equal(
    await promo.locator('[data-play="true"]').count(),
    1,
    "the card animates whenever the popup opens",
  );
  const gridBefore = await page.locator(".article-grid").boundingBox();
  await page.keyboard.press("Escape");
  assert.equal(await promo.count(), 0);
  assert.equal(await page.evaluate(() => document.body.style.overflow), "");
  assert.deepEqual(
    await page.locator(".article-grid").boundingBox(),
    gridBefore,
    "closing the popup must not shift the feed",
  );
  await page.reload();
  assert.equal(await promo.count(), 0, "dismissal survives navigation");
  // Leave the original auth suite independent of this preview draft.
  if (extension) {
    await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-promo-dismissed"));
    await page.reload();
    await promo.getByRole("button", { name: "Create your dev card" }).click();
  } else {
    await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-draft"));
  }
}

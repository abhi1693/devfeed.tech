import assert from "node:assert/strict";

const featuredPath = /\/api\/v1\/users\/asaharan(?:\?.*)?$/;
const featured = (route) =>
  route.fulfill({
    json: {
      profile: {
        username: "asaharan",
        display_name: "Abhimanyu Saharan",
        avatar_url: null,
        bio: "Open-source builder, homelabber, and founder building DevFeed. I turn ideas into software, build things for the web, and automate everything I can.",
        location: "India",
        stack: [
          {
            topic_id: "kubernetes",
            name: "Kubernetes",
            kind: "platform",
            section: "primary",
            logo_url: null,
          },
        ],
        reading_streak: { current_days: 3, longest_days: 19, total_days: 42 },
      },
    },
  });

export async function checkDevCardPromo(page, screenshotPrefix, { extension = false } = {}) {
  await page.context().route(featuredPath, featured);
  await page.clock.install();
  async function reloadBeforeReveal(checkMinimum = false) {
    await page.reload();
    const preview = page.locator('dialog[aria-label="Your dev card preview"]');
    await preview.waitFor({ state: "attached" });
    await page.waitForFunction(() =>
      document.querySelector('dialog[aria-label="Your dev card preview"] .dev-card-artwork'),
    );
    const elapsed = await page.evaluate(() => performance.now());
    if (checkMinimum) {
      assert.ok(elapsed < 29_000, "preview mounts before the minimum visit duration");
      await page.clock.fastForward(Math.floor(29_000 - elapsed));
      assert.equal(await preview.isVisible(), false, "no reveal before 30 seconds on site");
      await page.clock.fastForward(1000);
    } else {
      // Playwright's virtual performance clock continues across reloads.
      await page.clock.fastForward(Math.max(1200, 30_000 - elapsed));
    }
  }
  await reloadBeforeReveal(true);
  const promo = page.getByRole("region", { name: "Discover your dev card" });
  await promo.waitFor();
  const dialog = page.getByRole("dialog", { name: "Your dev card preview" });
  await promo.getByRole("img", { name: /^Dev card for Abhimanyu Saharan\b/ }).waitFor();
  await page.waitForFunction(() => {
    const lines = document.querySelectorAll("dialog[open] .dev-card-bio text:not([aria-hidden])");
    return lines.length > 1;
  });
  assert.equal(
    await promo
      .locator(".dev-card-bio text:not([aria-hidden])")
      .evaluateAll((lines) => lines.every((line) => line.getComputedTextLength() <= 480.5)),
    true,
    "The revealed card wraps its complete bio within the SVG canvas",
  );
  assert.equal(
    await promo.locator(".dev-card-artwork").evaluate((svg) => {
      const stats = svg.querySelector(".dev-card-stats").getBBox();
      return stats.y + stats.height <= svg.viewBox.baseVal.height;
    }),
    true,
    "The card height includes the wrapped bio and stats",
  );
  assert.equal(await promo.getByText("@asaharan’s live Dev Card", { exact: true }).count(), 1);
  assert.equal(await promo.getByText("Alex Morgan", { exact: true }).count(), 0);
  assert.equal(await promo.locator(".dev-card-stats").getByText("19", { exact: true }).count(), 1);
  await page.mouse.click(1, 1);
  assert.equal(await dialog.isVisible(), true, "outside clicks leave the card reveal open");
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
    await reloadBeforeReveal();
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
  await reloadBeforeReveal();
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
  const artwork = promo.locator("svg.dev-card-artwork");
  const dimensions = await artwork.evaluate((svg) => ({
    width: svg.clientWidth,
    height: svg.clientHeight,
    ratio: svg.viewBox.baseVal.height / svg.viewBox.baseVal.width,
  }));
  assert.ok(
    dimensions.width >= 390,
    "Desktop promo preserves the 400px card instead of shrinking it into the modal",
  );
  assert.ok(
    Math.abs(dimensions.height - dimensions.width * dimensions.ratio) < 2,
    "The modal follows the card's intrinsic aspect ratio",
  );
  const columns = await promo.evaluate((node) => {
    const stage = node.querySelector('[data-testid="dev-card-stage"]');
    return [
      stage.getBoundingClientRect().width,
      stage.nextElementSibling.getBoundingClientRect().width,
    ];
  });
  assert.ok(Math.abs(columns[0] - columns[1]) < 1, "Card and copy panels have equal width");
  await page.screenshot({ path: `${screenshotPrefix}-desktop.png` });
  const theme = await page.locator("html").getAttribute("class");
  await page.locator("html").evaluate((node) => node.classList.add("dark"));
  await page.screenshot({ path: `${screenshotPrefix}-dark.png`, animations: "allow" });
  await page.locator("html").evaluate((node, value) => {
    node.className = value ?? "";
  }, theme);
  const stage = promo.getByTestId("dev-card-stage");
  const box = await stage.boundingBox();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.4);
  assert.ok(await stage.locator('[style*="--tilt-y"]').count(), "mouse movement tilts the card");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(
    (node) =>
      node.getAnimations({ subtree: true }).every((animation) => animation.playState !== "running"),
    await promo.elementHandle(),
  );
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
  await page.waitForFunction(
    (input) => input === document.activeElement,
    await name.elementHandle(),
  );
  assert.equal(await name.evaluate((node) => node === document.activeElement), true);
  await name.fill("Maya Chen");
  await promo.getByLabel("Your technologies").fill("TypeScript");
  await promo.getByRole("button", { name: "Add TypeScript", exact: true }).click();
  assert.ok(await promo.getByRole("img", { name: "Dev card for Maya Chen" }).count());
  assert.equal(await promo.getByText("Current streak", { exact: true }).count(), 0);
  const signup = promo.getByRole("link", { name: "Save my dev card" });
  const url = new URL(await signup.getAttribute("href"), "https://devfeed.tech");
  assert.equal(url.searchParams.has("register"), false);
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
  await reloadBeforeReveal();
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
  const unavailablePage = await page.context().newPage();
  const unavailableCard = (route) =>
    route.fulfill({ status: 503, json: { detail: "Unavailable" } });
  await page.context().route(featuredPath, unavailableCard);
  const unavailableResponse = unavailablePage.waitForResponse(
    (response) => featuredPath.test(response.url()) && response.status() === 503,
  );
  await unavailablePage.goto(page.url());
  await unavailableResponse;
  await unavailablePage.clock.fastForward(35_000);
  assert.equal(
    await unavailablePage.locator('dialog[aria-label="Your dev card preview"]').count(),
    0,
    "An unavailable featured card must not open a promotion",
  );
  assert.notEqual(await unavailablePage.evaluate(() => document.body.style.overflow), "hidden");
  await page.context().unroute(featuredPath, unavailableCard);

  await unavailablePage.close();
  // Leave the original auth suite independent of this preview draft.
  if (extension) {
    await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-promo-dismissed"));
    await reloadBeforeReveal();
    await promo.getByRole("button", { name: "Create your dev card" }).click();
  } else {
    await page.evaluate(() => sessionStorage.removeItem("devfeed:dev-card-draft"));
  }
  await page.context().unroute(featuredPath, featured);
}

export async function checkUnclaimedDevCardPromo(
  page,
  screenshotPrefix,
  { extension = false } = {},
) {
  const originalUrl = page.url();
  const feedUrl = extension ? `${originalUrl.split("#")[0]}#/` : new URL("/", originalUrl).href;
  const profilePath = "**/api/v1/user/settings/profile";
  const feedPath = "**/api/v1/user/feed?**";
  const emptyFeed = (route) =>
    route.fulfill({
      json: { status: "ready", has_interests: true, items: [], next_cursor: null, reasons: {} },
    });
  let username = "claimed-reader";
  const profile = (route) =>
    route.fulfill({
      json: {
        display_name: "Promo Reader",
        avatar_url: null,
        username,
        visibility: {
          public: false,
          location: false,
          stack: false,
          heatmap: false,
          achievements: false,
        },
      },
    });
  await page.route(profilePath, profile);
  await page.route(featuredPath, featured);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.evaluate(() => {
    sessionStorage.removeItem("devfeed:dev-card-draft");
    // Dismissing the anonymous modal must not suppress an unclaimed account's modal.
    sessionStorage.setItem("devfeed:dev-card-promo-dismissed", "true");
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith("devfeed:dev-card-promo-dismissed:")) sessionStorage.removeItem(key);
    }
  });
  const dialog = page.getByRole("dialog", { name: "Your dev card preview" });
  const menu = page.locator('button[aria-label="User menu: Promo Reader"]');
  try {
    await page.goto(feedUrl);
    if (extension) await page.reload();
    await menu.waitFor();
    await page.clock.fastForward(35_000);
    assert.equal(await dialog.count(), 0, "A claimed username hides the modal even when private");

    username = null;
    await page.reload();
    await menu.waitFor();
    await page.locator('dialog[aria-label="Your dev card preview"]').waitFor({ state: "attached" });
    await page.clock.fastForward(35_000);
    await dialog.waitFor();
    assert.equal(await dialog.evaluate((node) => node.matches(":modal")), true);
    await dialog.getByRole("img", { name: /^Dev card for Abhimanyu Saharan\b/ }).waitFor();
    const finish = dialog.getByRole("link", { name: "Finish your dev card" });
    assert.equal(
      await finish.getAttribute("href"),
      extension ? "#/settings/profile" : "/settings/profile",
    );
    assert.equal(await dialog.getByRole("link", { name: "Save my dev card" }).count(), 0);
    await page.screenshot({ path: `${screenshotPrefix}-signed-in.png`, animations: "disabled" });
    await finish.click();
    await page.getByRole("textbox", { name: "Username", exact: true }).waitFor();
    assert.equal(await dialog.count(), 0, "Finishing the card opens profile settings");

    await page.route(feedPath, emptyFeed);
    await page.goto(feedUrl);
    await menu.waitFor();
    await page.getByRole("heading", { name: "No recommendations yet", exact: true }).waitFor();
    await page.locator('dialog[aria-label="Your dev card preview"]').waitFor({ state: "attached" });
    await page.clock.fastForward(35_000);
    await dialog.getByRole("button", { name: "Dismiss dev card preview" }).click();
    assert.equal(await dialog.count(), 0);
    assert.notEqual(await page.evaluate(() => document.body.style.overflow), "hidden");
    await page.reload();
    await menu.waitFor();
    await page.clock.fastForward(35_000);
    assert.equal(await dialog.count(), 0, "Signed-in dismissal survives navigation");
  } finally {
    await page.unroute(profilePath, profile);
    await page.unroute(featuredPath, featured);
    await page.unroute(feedPath, emptyFeed);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.goto(originalUrl);
  }
}

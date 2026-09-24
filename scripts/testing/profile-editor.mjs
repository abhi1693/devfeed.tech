import assert from "node:assert/strict";

export async function checkProfileEditor(page, screenshotPrefix) {
  const bio = page.getByLabel("Short bio", { exact: true });
  const originalBio = await bio.inputValue();
  await checkReadingRefresh(page);
  assert.equal(await page.locator(".profile-direct details").count(), 0);
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(
    await page.locator(".profile-direct-heading p, .profile-direct-visibility p").count(),
    0,
  );
  assert.equal(
    await page.getByText("Username claimed. It can’t be changed.", { exact: true }).count(),
    0,
  );
  const username = page.getByRole("textbox", { name: "Username", exact: true });
  if (await username.evaluate((input) => input.readOnly)) {
    assert.equal(await username.getAttribute("aria-describedby"), null);
    assert.equal(await page.locator("#profile-username-help").count(), 0);
  }
  for (const name of ["Location", "Technologies", "Reading activity"]) {
    assert.equal(await page.getByRole("checkbox", { name, exact: true }).count(), 0);
  }
  assert.equal(
    await page.getByRole("checkbox", { name: "Make my profile public", exact: true }).count(),
    1,
  );
  assert.equal(await page.getByRole("textbox", { name: /^Link \d+ label$/ }).count(), 0);
  for (const label of [
    "Display name",
    "Username",
    "Avatar URL",
    "Location",
    "About",
    "Link 1 URL",
    "Find a technology",
  ]) {
    assert.equal(
      await page
        .getByRole(label === "Find a technology" ? "searchbox" : "textbox", {
          name: label,
          exact: true,
        })
        .isVisible(),
      true,
    );
  }
  await bio.fill("Building tools and exploring TypeScript.");
  await page
    .getByLabel("Link 1 URL", { exact: true })
    .pressSequentially("https://github.com/reader");
  assert.equal(
    await page.getByLabel("Link 1 URL", { exact: true }).inputValue(),
    "https://github.com/reader",
  );
  const site = page.locator(".profile-link-icon").first();
  assert.equal(await site.getAttribute("aria-label"), "GitHub");
  assert.equal(await page.locator(".profile-direct-link-site").count(), 0);
  const iconBox = await site.boundingBox();
  const inputBox = await page.getByLabel("Link 1 URL", { exact: true }).boundingBox();
  assert.ok(iconBox.x > inputBox.x && iconBox.x + iconBox.width < inputBox.x + inputBox.width);
  assert.ok(iconBox.y >= inputBox.y && iconBox.y + iconBox.height <= inputBox.y + inputBox.height);
  await page.getByLabel("Link 1 URL", { exact: true }).fill("https://gitlab.com/reader");
  assert.equal(await site.getAttribute("aria-label"), "GitLab");
  await page.getByLabel("Link 1 URL", { exact: true }).fill("https://github.com.example.org/me");
  assert.equal(await site.getAttribute("aria-label"), "Website");
  await page.getByLabel("Link 1 URL", { exact: true }).fill("https://github.com/reader");
  const addLink = page.getByRole("button", { name: "Add link", exact: true });
  await addLink.hover();
  await page.waitForFunction(
    (button) => getComputedStyle(button).backgroundColor !== "rgba(0, 0, 0, 0)",
    await addLink.elementHandle(),
  );
  const hover = await addLink.evaluate((button) => {
    const style = getComputedStyle(button);
    const bounds = button.getBoundingClientRect();
    const label = [...button.childNodes].find((node) => node.textContent.trim() === "Add link");
    const range = document.createRange();
    range.selectNodeContents(label);
    return {
      left: parseFloat(style.paddingLeft),
      right: parseFloat(style.paddingRight),
      textInset: bounds.right - range.getBoundingClientRect().right,
      background: style.backgroundColor,
    };
  });
  assert.equal(hover.left, 10);
  assert.equal(hover.right, 10);
  assert.ok(hover.textInset >= 9.5, "Hover background includes padding after the label");
  assert.notEqual(hover.background, "rgba(0, 0, 0, 0)");
  if (screenshotPrefix)
    await addLink.screenshot({ path: `${screenshotPrefix}-add-link-hover.png` });
  await page.mouse.move(0, 0);
  const search = page.getByLabel("Find a technology", { exact: true });
  await search.fill("TypeScript");
  const choice = page.locator("button[data-add-topic]").first();
  await choice.waitFor();
  const technology = (await choice.getAttribute("aria-label")).slice(4);
  await search.press("Enter");
  await page.getByRole("button", { name: `Remove ${technology}`, exact: true }).waitFor();
  const technologyIcon = page.locator('.dev-card-preview [data-technology-icon="brand"]').first();
  await technologyIcon.waitFor({ state: "visible" });
  assert.ok((await technologyIcon.locator("path").getAttribute("d")).length > 20);
  assert.equal(await technologyIcon.getAttribute("width"), "36");
  assert.equal(await technologyIcon.locator("..").locator("text").count(), 0);
  assert.equal(await search.inputValue(), "");
  assert.equal(await search.evaluate((el) => el === document.activeElement), true);
  await page.getByLabel(`Usage for ${technology}`, { exact: true }).selectOption("learning");
  await page.getByLabel(`Since year for ${technology}`, { exact: true }).fill("2022");
  if (screenshotPrefix) {
    await page.evaluate(() => {
      document.activeElement?.blur();
      scrollTo(0, 0);
    });
    await page.screenshot({ path: `${screenshotPrefix}-desktop.png`, fullPage: true });
    const viewport = page.viewportSize();
    for (const width of [1280, 2048]) {
      await page.setViewportSize({ width, height: 1152 });
      const positions = await page.locator(".profile-editor-layout").evaluate((layout) => {
        const bounds = layout.getBoundingClientRect();
        const form = layout.querySelector(".profile-direct").getBoundingClientRect();
        const preview = layout.querySelector(".dev-card-preview").getBoundingClientRect();
        const start = form.right + parseFloat(getComputedStyle(layout).columnGap);
        return {
          actual: preview.x + preview.width / 2,
          expected: (start + bounds.right) / 2,
          formWidth: form.width,
        };
      });
      assert.ok(
        Math.abs(positions.actual - positions.expected) <= 1,
        "Card is centered beside the form, not pinned to the far right",
      );
      assert.ok(positions.formWidth <= 780, "Wide screens keep the form readable");
    }
    await page.screenshot({ path: `${screenshotPrefix}-wide.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${screenshotPrefix}-mobile.png`, fullPage: true });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await page.setViewportSize(viewport);
  }
  await page.getByRole("button", { name: "Discard changes", exact: true }).click();
  assert.equal(await bio.inputValue(), originalBio);
  assert.equal(
    await page.getByRole("button", { name: `Remove ${technology}`, exact: true }).count(),
    0,
  );
}

async function checkReadingRefresh(page) {
  const name = page.getByLabel("Display name", { exact: true });
  const original = await name.inputValue();
  const profilePath = "**/api/v1/user/settings/profile";
  const handler = (route) =>
    route.fulfill({
      json: {
        display_name: "Server profile",
        avatar_url: null,
        reading_streak: {
          current_days: 13,
          longest_days: 17,
          total_days: 29,
          last_read_date: null,
        },
      },
    });
  await page.context().route(profilePath, handler);
  try {
    await name.fill("Unsaved reading refresh");
    await page.clock.setSystemTime(await page.evaluate(() => Date.now() + 61_000));
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    const card = page.locator(".dev-card-artwork");
    await page.waitForFunction(() =>
      document.querySelector(".dev-card-preview svg desc")?.textContent.includes("day streak: 13."),
    );
    assert.match(await card.textContent(), /best streak: 17\./);
    assert.match(await card.textContent(), /days reading: 29\./);
    assert.equal(await name.inputValue(), "Unsaved reading refresh");
    assert.equal(await page.getByRole("button", { name: "Download card" }).isDisabled(), true);
    await page.getByRole("button", { name: "Discard changes", exact: true }).click();
    assert.equal(await name.inputValue(), original);
    assert.match(await card.textContent(), /day streak: 13\./);
    assert.equal(
      await page.getByRole("button", { name: "Save changes", exact: true }).isDisabled(),
      true,
    );
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download card" }).click();
    assert.equal(await (await download).failure(), null);
  } finally {
    await page.context().unroute(profilePath, handler);
  }
}

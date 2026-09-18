import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

// Run the same modal journey against the website and both built extensions.
export async function checkFeedOnboarding(page, home, output) {
  await mkdir(output, { recursive: true });
  await page.goto(home);
  const dialog = page.getByRole("dialog", { name: "Choose your topics" });
  await dialog.waitFor();
  assert.equal(await page.locator("h1").innerText(), "My feed");
  const search = dialog.getByRole("searchbox", { name: "Search topics" });
  const save = dialog.getByRole("button", { name: "Save", exact: true });
  await dialog.getByRole("checkbox", { name: "TypeScript", exact: true }).waitFor();
  assert.deepEqual(
    await dialog
      .locator("label")
      .allTextContents()
      .then((names) => names.slice(0, 3)),
    ["TypeScript", "Python", "React"],
  );
  assert.ok(await save.isDisabled());
  await dialog.getByText("Couldn’t load more topics.").waitFor();
  await dialog.getByRole("checkbox", { name: "TypeScript", exact: true }).check();
  await dialog.getByRole("button", { name: "Try again", exact: true }).click();
  await dialog.getByText("Couldn’t load more topics.").waitFor({ state: "detached" });
  assert.ok(await dialog.getByRole("checkbox", { name: "TypeScript", exact: true }).isChecked());
  await dialog.getByRole("checkbox", { name: "TypeScript", exact: true }).uncheck();
  await page.screenshot({ path: `${output}/onboarding-desktop.png` });
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "detached" });
  await page.reload();
  await dialog.getByRole("checkbox", { name: "TypeScript", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await search.fill("no matching topic");
  await dialog.getByText("No topics match your search.").waitFor();
  await search.fill("typescript");
  const choice = dialog.getByRole("checkbox", { name: "TypeScript", exact: true });
  await choice.focus();
  await page.keyboard.press("Space");
  assert.ok(await choice.isChecked());
  await search.fill("");
  await dialog.getByRole("checkbox", { name: "Python", exact: true }).check();
  assert.ok(await save.isDisabled());
  await dialog.getByRole("checkbox", { name: "React", exact: true }).check();
  assert.equal(await save.isDisabled(), false);
  await dialog.getByRole("checkbox", { name: "Docker", exact: true }).check();
  await dialog.getByText("4 selected", { exact: true }).waitFor();
  await dialog.getByRole("checkbox", { name: "Docker", exact: true }).uncheck();
  await page.screenshot({ path: `${output}/onboarding-mobile.png` });
  const bounds = await dialog.boundingBox();
  const buttonBounds = await save.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0 && bounds.y + bounds.height <= 844);
  assert.ok(buttonBounds && buttonBounds.y + buttonBounds.height <= 844);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await save.focus();
  await page.evaluate(() => document.querySelector(".sidebar a")?.focus());
  assert.ok(await dialog.evaluate((element) => element.contains(document.activeElement)));
  await save.click();
  await dialog.getByText("Couldn’t save your topics. Please try again.").waitFor();
  assert.equal(await dialog.getByRole("checkbox", { checked: true }).count(), 3);
  await save.click();
  await dialog.waitFor({ state: "detached" });
  await page.locator(".article-card").first().waitFor({ timeout: 15000 });
  await page.reload();
  await page.locator(".article-card").first().waitFor();
  assert.equal(await dialog.count(), 0);
  await page.setViewportSize({ width: 1440, height: 1000 });
}

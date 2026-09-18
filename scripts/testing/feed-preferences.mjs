import assert from "node:assert/strict";

export async function checkLanguagePreferences(page, base) {
  await page.goto(`${base}/settings/feed`);
  const english = page.getByRole("checkbox", { name: "English", exact: true });
  await english.waitFor();
  assert.equal(await english.isChecked(), true);
  await english.uncheck();
  await page.getByText("Select at least one language.", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "Save changes", exact: true }).isDisabled(),
    true,
  );
  await english.check();
  await page.getByRole("searchbox", { name: "Find a language" }).fill("French");
  await page.getByRole("checkbox", { name: "French", exact: true }).check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("Feed settings saved.", { exact: true }).waitFor();
  await page.reload();
  assert.equal(
    await page.getByRole("checkbox", { name: "English", exact: true }).isChecked(),
    true,
  );
  assert.equal(await page.getByRole("checkbox", { name: "French", exact: true }).isChecked(), true);
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("Feed settings saved.", { exact: true }).waitFor();
}

export async function checkFeedSort(page, base, personal = false) {
  await page.goto(`${base}${personal ? "/" : "/latest"}`);
  assert.equal(await page.getByRole("combobox", { name: "Language", exact: true }).count(), 0);
  await page.getByRole("combobox", { name: "Sort by", exact: true }).click();
  await page.getByRole("option", { name: "Most liked", exact: true }).click();
  await page.waitForURL(/sort=most_liked/);
  await page.locator(".article-card").first().waitFor();
  assert.equal(
    await page.getByRole("combobox", { name: "Sort by", exact: true }).textContent(),
    "Most liked",
  );
}

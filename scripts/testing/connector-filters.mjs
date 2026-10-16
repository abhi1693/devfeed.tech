import assert from "node:assert/strict";

/** Both portals render the same response-linked filters and request parameter controls. */
export async function assertConnectorFilterColors(page, { responseCount, screenshotPrefix }) {
  const originalViewport = page.viewportSize();
  const summary = page.getByText("Filters and query parameters", { exact: true });
  const wasOpen = await summary.evaluate((node) => node.parentElement.open);
  if (!wasOpen) await summary.click();
  const json = page.getByRole("textbox", { name: "API response JSON", exact: true });
  const path = page.getByRole("textbox", { name: "Filter 1 path", exact: true });
  const values = page.getByRole("textbox", { name: "Filter 1 values", exact: true });
  const line = (field) => json.locator(`[data-mappings~="${field}"]`);
  const swatch = async (input) => (await input.getAttribute("class")).match(/border-\w+-600/)?.[0];
  assert.equal(await path.count(), 0, "The isolated color journey starts without saved filters");
  assert.equal(
    await page.getByRole("textbox", { name: "Parameter 1 name", exact: true }).count(),
    0,
    "The isolated color journey starts without request parameters",
  );
  const requests = responseCount();
  await page.getByRole("button", { name: "Add filter", exact: true }).click();
  await path.fill("categories");
  await values.fill("Developer Tools");
  await line("filters.0.values").waitFor();
  assert.ok(await swatch(path));
  assert.equal(await swatch(values), await swatch(path));
  assert.match(await line("filters.0.path").innerText(), /"categories": \[/);
  assert.match(await line("filters.0.values").innerText(), /"Developer Tools"/);
  assert.equal(
    await line("filters.0.values").count(),
    1,
    "Only the matching array value is colored",
  );

  await values.fill("Unknown category");
  await page.waitForFunction(() => !document.querySelector('[data-mappings~="filters.0.values"]'));
  await page.getByLabel("Filter 1 values not found in this sample", { exact: true }).waitFor();
  assert.equal(await swatch(values), undefined, "Absent values remain neutral");
  assert.ok(await swatch(path), "A valid path retains its own response match");
  await path.fill("missing.categories");
  await page.getByLabel("Filter 1 path not found in this sample", { exact: true }).waitFor();
  assert.equal(await line("filters.0.path").count(), 0);
  assert.equal(await swatch(path), undefined, "Absent paths remain neutral");
  assert.notEqual(await path.getAttribute("aria-invalid"), "true");
  await path.fill("categories");
  await values.fill("Developer Tools");
  await line("filters.0.values").waitFor();

  await page.getByRole("button", { name: "Add filter", exact: true }).click();
  await page.getByRole("textbox", { name: "Filter 2 values", exact: true }).fill("Productivity");
  await line("filters.1.values").waitFor();
  await page.getByRole("button", { name: "Remove filter 1", exact: true }).click();
  assert.equal(await values.inputValue(), "Productivity");
  assert.match(await line("filters.0.values").innerText(), /"Productivity"/);
  assert.equal(
    await line("filters.1.values").count(),
    0,
    "Removed filters leave no stale highlights",
  );
  await values.fill("Developer Tools");
  await line("filters.0.values").waitFor();
  await page.waitForTimeout(850);
  assert.equal(
    responseCount(),
    requests,
    "Filter edits recolor the existing response without refetching",
  );

  await page.getByRole("button", { name: "Add query parameter", exact: true }).click();
  const parameterName = page.getByRole("textbox", { name: "Parameter 1 name", exact: true });
  const parameterValue = page.getByRole("textbox", { name: "Parameter 1 value", exact: true });
  await parameterName.fill("category");
  await parameterValue.fill("Developer Tools");
  assert.equal(
    await parameterName.getAttribute("title"),
    "Query parameter 1: configured for requests",
  );
  assert.equal(
    await parameterValue.getAttribute("title"),
    "Query parameter 1: configured for requests",
  );
  assert.ok(await swatch(parameterName));
  assert.equal(await swatch(parameterName), await swatch(parameterValue));
  assert.equal(await json.locator('[data-mappings^="parameters."]').count(), 0);
  assert.equal(
    await parameterValue.locator("..").locator("svg.lucide-check").count(),
    0,
    "A configured request parameter does not claim response API acceptance",
  );

  await page.waitForTimeout(850);
  await json.waitFor();
  await line("filters.0.values").waitFor();
  await page.getByRole("heading", { name: "API response", exact: true }).click();
  const originalDark = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (dark) => document.documentElement.classList.toggle("dark", dark),
      theme === "dark",
    );
    for (const [size, width, height] of [
      ["desktop", 1440, 1000],
      ["mobile", 390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      await path.scrollIntoViewIfNeeded();
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),
        false,
        "Colored filter controls fit the viewport",
      );
      assert.notEqual(
        await path.evaluate((node) => getComputedStyle(node).borderColor),
        "rgba(0, 0, 0, 0)",
      );
      await page.screenshot({ path: `${screenshotPrefix}-${size}-${theme}.png`, fullPage: true });
    }
  }
  await page.evaluate(
    (dark) => document.documentElement.classList.toggle("dark", dark),
    originalDark,
  );
  await page.setViewportSize(originalViewport);
  await page.getByRole("button", { name: "Remove parameter 1", exact: true }).click();
  await page.getByRole("button", { name: "Remove filter 1", exact: true }).click();
  assert.equal(await path.count(), 0, "Filter changes were removed from the draft");
  assert.equal(
    await parameterName.count(),
    0,
    "Request parameter changes were removed from the draft",
  );
  if (!wasOpen) await summary.click();
}

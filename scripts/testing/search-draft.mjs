import assert from "node:assert/strict";

export async function prepareSearchDraft(page) {
  const search = page.getByRole("searchbox");
  await search.click();
  // Hold an IME composition so the draft does not submit through the debounce
  // while we exercise loading completion independently of route navigation.
  await search.dispatchEvent("compositionstart");
  await search.fill("Streaming search draft");
  await search.evaluate((field) => {
    field.dataset.retainedDraft = "yes";
    field.setSelectionRange(4, 10);
  });
}

export async function checkSearchDraft(page) {
  assert.equal(await page.locator("header.topbar").count(), 1);
  assert.equal(await page.getByRole("main").count(), 1);
  assert.deepEqual(
    await page.getByRole("searchbox").evaluate((field) => ({
      retained: field.dataset.retainedDraft,
      value: field.value,
      focused: document.activeElement === field,
      start: field.selectionStart,
      end: field.selectionEnd,
    })),
    { retained: "yes", value: "Streaming search draft", focused: true, start: 4, end: 10 },
    "loading completion preserves the search input, draft, focus and selection",
  );
}

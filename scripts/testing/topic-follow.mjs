import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

export async function checkTopicFollow(page, topicUrl, filteredUrl, output) {
  const reads = [];
  const record = (request) => {
    if (
      new URL(request.url()).pathname === "/api/v1/user/preferences" &&
      request.method() === "GET"
    )
      reads.push(request.url());
  };
  page.on("request", record);
  await page.goto(topicUrl);
  await page.locator(".article-card").first().waitFor();
  const header = page.getByRole("region", { name: "Feed controls" });
  await header.getByRole("button", { name: "Following", exact: true }).click();
  const follow = header.getByRole("button", { name: "Follow", exact: true });
  await follow.waitFor();
  await follow.click();
  await header.getByRole("alert").waitFor();
  assert.equal(await follow.getAttribute("aria-pressed"), "false");
  await follow.click();
  await header.getByRole("button", { name: "Following", exact: true }).waitFor();
  await mkdir(output, { recursive: true });
  await page.screenshot({ path: `${output}/topic-follow-desktop.png` });
  assert.ok(reads.length <= 1, "Topic page uses at most one preferences request");
  const initialReads = reads.length;
  await page.locator(".card-open-link").first().click();
  await page.locator("#article-preview-title").waitFor();
  const preview = page.locator(".topic-brief-actions");
  await preview.getByRole("button", { name: "Following", exact: true }).click();
  await preview.getByRole("button", { name: "Follow", exact: true }).waitFor();
  await page.getByRole("button", { name: "Close preview", exact: true }).click();
  await header.getByRole("button", { name: "Follow", exact: true }).waitFor();
  await header.getByRole("button", { name: "Follow", exact: true }).click();
  await header.getByRole("button", { name: "Following", exact: true }).waitFor();
  assert.equal(
    reads.length,
    initialReads,
    "Preview and follow mutations reuse the shared preferences",
  );
  page.off("request", record);
  await page.goto(filteredUrl);
  const following = header.getByRole("button", { name: "Following", exact: true });
  await following.waitFor();
  await page.locator(".article-card").first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await following.isVisible());
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: `${output}/topic-follow-mobile.png` });
  await page.reload();
  await following.waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
}

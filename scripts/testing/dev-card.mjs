import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

export async function checkDevCard(page, prefix) {
  const preview = page.getByRole("complementary", { name: "Dev card preview", exact: true });
  const name = page.getByRole("textbox", { name: "Display name", exact: true });
  const originalName = await name.inputValue();
  await name.fill("Abhimanyu Saharan");
  const nameLines = preview.locator(".dev-card-name text:not([aria-hidden])");
  assert.deepEqual(await nameLines.allTextContents(), ["Abhimanyu Saharan"]);
  assert.equal(
    await preview.locator("[textLength]").count(),
    0,
    "Card text must never stretch or squash glyphs",
  );
  assert.equal(
    await nameLines.evaluateAll((nodes) =>
      nodes.every((node) => {
        const box = node.getBBox();
        return getComputedStyle(node).fontSize === "44px" && box.width <= 480;
      }),
    ),
    true,
    "Names wrap at their measured width with consistent typography",
  );
  await preview.locator(".dev-card-artwork").click();
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(
    await page.getByRole("button", { name: "Open dev card preview", exact: true }).count(),
    0,
  );
  assert.equal(await preview.getByText("Ready to share", { exact: true }).count(), 0);
  const headingBounds = await preview
    .getByRole("heading", { name: "Your Dev Card", exact: true })
    .boundingBox();
  const cardBounds = await preview.locator(".dev-card-artwork").boundingBox();
  assert.ok(
    Math.abs(headingBounds.x + headingBounds.width / 2 - cardBounds.x - cardBounds.width / 2) <= 1,
    "Title is centered above the card",
  );
  await page.screenshot({ path: `${prefix}-long-name.png` });
  await name.fill(originalName);
  const bio = page.getByRole("textbox", { name: "Short bio", exact: true });
  const originalBio = await bio.inputValue();
  for (const fullBio of [
    "I build tools for developers, contribute to open source, and enjoy exploring distributed systems. Currently learning Rust and sharing everything I learn as I go.",
    "W".repeat(160),
    "界".repeat(160),
  ].map((text) => text.slice(0, 160))) {
    await bio.fill(fullBio);
    const lines = preview.locator(".dev-card-bio text:not([aria-hidden])");
    assert.equal(
      (await lines.allTextContents()).join("").replace(/\s/gu, ""),
      fullBio.replace(/\s/gu, ""),
      "The visible bio keeps every character",
    );
    assert.equal(
      await lines.evaluateAll((nodes) =>
        nodes.every(
          (node) => node.getBBox().width <= 480.1 && getComputedStyle(node).fontSize === "17px",
        ),
      ),
      true,
      "Bio wraps without clipping or shrinking",
    );
    const bounds = await preview.locator(".dev-card-bio").boundingBox();
    const footer = await preview.locator(".dev-card-brand").boundingBox();
    assert.ok(bounds.y + bounds.height < footer.y, "Full bio stays above the footer");
  }
  await bio.fill("Building a more thoughtful web.");
  assert.ok((await preview.textContent()).includes("Building a more thoughtful web."));
  assert.equal(await preview.getByRole("button", { name: "Download card" }).isDisabled(), true);
  await page.getByRole("button", { name: "Discard changes", exact: true }).click();
  assert.equal(await bio.inputValue(), originalBio);
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: `${prefix}-settings.png`, fullPage: true });

  const menu = page.getByRole("button", { name: /^User menu:/ });
  await menu.click();
  assert.equal(await page.getByRole("menuitem", { name: "Dev card", exact: true }).count(), 0);
  await page.getByRole("menuitem", { name: "Profile settings", exact: true }).click();
  await preview.waitFor();
  assert.match(page.url(), /(?:\/|#\/)settings\/profile$/);
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal((await preview.textContent()).includes("Building a more thoughtful web."), false);
  const badgeText = await preview.locator(".dev-card-artwork text").allTextContents();
  for (const filler of [
    "DEV CARD",
    "DevFeed community",
    "devfeed.tech",
    "The developer community.",
    "Never done learning.",
    "Read. Build. Repeat.",
    "Good ideas start with curiosity.",
  ]) {
    assert.equal(
      badgeText.includes(filler),
      false,
      `Badge must not contain generic copy: ${filler}`,
    );
  }
  assert.equal(await preview.locator(".dev-card-share-note").count(), 0);
  assert.equal(await preview.locator(".dev-card-brand image[data-brand-mark]").count(), 1);
  assert.equal(await preview.locator(".dev-card-brand text").getAttribute("font-size"), "22");
  const brandGap = await preview.locator(".dev-card-brand").evaluate((brand) => {
    const mark = brand.querySelector("image").getBBox();
    const wordmark = brand.querySelector("text").getBBox();
    return wordmark.x - (mark.x + mark.width);
  });
  assert.ok(brandGap >= 14, "The mark and wordmark have clear separation");
  assert.equal(
    (await preview.locator(".dev-card-bio text:not([aria-hidden])").allTextContents())
      .join("")
      .replace(/\s/gu, ""),
    originalBio.replace(/\s/gu, ""),
  );
  assert.equal(await preview.locator(".dev-card-stats rect").count(), 0);
  assert.equal(await preview.locator('.dev-card-grid[aria-hidden="true"] rect').count(), 275);
  assert.equal(
    await preview.locator(".dev-card-technologies > g").evaluateAll((items) =>
      items.every((item) => {
        const icon = item.querySelector("image[data-technology-logo]");
        const label = item.querySelector("text[data-technology-fallback]");
        return (
          Boolean(item.getAttribute("aria-label")) &&
          Boolean(label?.textContent) &&
          (icon
            ? label.getAttribute("visibility") === "hidden" && icon.getAttribute("width") === "36"
            : label.getAttribute("visibility") === "visible")
        );
      }),
    ),
    true,
    "Stack items use their configured topic logo and fall back to their name",
  );
  const exportHeight = await preview
    .locator(".dev-card-artwork")
    .evaluate((svg) => svg.viewBox.baseVal.height * 2);
  await page.screenshot({ path: `${prefix}-desktop.png` });
  const downloadPromise = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download card", exact: true }).click();
  const download = await downloadPromise;
  assert.match(download.suggestedFilename(), /^devfeed-.*\.png$/);
  assert.equal(await download.failure(), null);
  await download.saveAs(`${prefix}-card.png`);
  const bytes = await readFile(`${prefix}-card.png`);
  assert.equal(bytes.subarray(1, 4).toString(), "PNG");
  assert.equal(bytes.readUInt32BE(16), 1120);
  assert.equal(bytes.readUInt32BE(20), exportHeight);
  assert.ok(bytes.length > 20000, "Export contains the rendered design, not an empty canvas");
  const matchesTheme = await page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0);
    // Clear areas on the card surface and the colored artwork must match theme tokens.
    const theme = getComputedStyle(document.documentElement);
    return [
      ["--card", 20, 680],
      ["--chart-1", 874, 234],
    ].every(([token, x, y]) => {
      const pixel = [...ctx.getImageData(x, y, 1, 1).data];
      const expected = theme.getPropertyValue(token).trim();
      ctx.fillStyle = expected;
      ctx.fillRect(0, 0, 1, 1);
      return JSON.stringify(pixel) === JSON.stringify([...ctx.getImageData(0, 0, 1, 1).data]);
    });
  }, bytes.toString("base64"));
  assert.equal(matchesTheme, true, "PNG colors come from the active shared theme");
  const hasBrandMark = await page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0);
    const surface = [...ctx.getImageData(20, 680, 1, 1).data];
    const markY =
      Number(document.querySelector(".dev-card-preview [data-brand-mark]").getAttribute("y")) * 2;
    const markX =
      Number(document.querySelector(".dev-card-preview [data-brand-mark]").getAttribute("x")) * 2;
    const mark = ctx.getImageData(markX, markY, 84, 84).data;
    let painted = 0;
    for (let i = 0; i < mark.length; i += 4) {
      if (
        Math.abs(mark[i] - surface[0]) +
          Math.abs(mark[i + 1] - surface[1]) +
          Math.abs(mark[i + 2] - surface[2]) >
        60
      )
        painted++;
    }
    return painted > 500;
  }, bytes.toString("base64"));
  assert.equal(hasBrandMark, true, "The real brand icon is embedded in the downloaded PNG");
  await preview.getByText("Your card is downloaded.", { exact: true }).waitFor();
  assert.equal(await preview.getByRole("button", { name: "Copy image", exact: true }).count(), 0);

  const viewport = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  await preview.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${prefix}-mobile.png` });
  const box = await preview.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390);
  const artwork = await preview.locator(".dev-card-artwork").boundingBox();
  const downloadBox = await preview
    .getByRole("button", { name: "Download card", exact: true })
    .boundingBox();
  assert.ok(artwork.y + artwork.height < downloadBox.y, "Actions must not overlap the card");
  await page.setViewportSize(viewport);
}

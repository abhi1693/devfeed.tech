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
  assert.ok(brandGap >= 0 && brandGap <= 4, "The mark and wordmark form a compact lockup");
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
  const wasDark = await page.locator("html").evaluate((node) => node.classList.contains("dark"));
  await page.locator("html").evaluate((node) => node.classList.add("dark"));
  assert.equal(
    await preview.locator("image[data-technology-logo]").evaluateAll((images) =>
      images.every((image) => {
        const tile = image.parentElement.querySelector("rect");
        const fallback = image.parentElement.querySelector("text[data-technology-fallback]");
        return (
          getComputedStyle(tile).fill === "rgb(255, 255, 255)" &&
          getComputedStyle(fallback).fill === "rgb(38, 38, 38)"
        );
      }),
    ),
    true,
    "Dark cards keep logo tiles white so dark artwork remains visible",
  );
  await page.locator("html").evaluate((node, dark) => node.classList.toggle("dark", dark), wasDark);
  await page.screenshot({ path: `${prefix}-desktop.png` });
  // A failed remote logo must expose its label in the serialized PNG source.
  await preview.locator(".dev-card-artwork").evaluate((svg) => {
    const ns = "http://www.w3.org/2000/svg";
    const group = document.createElementNS(ns, "g");
    group.setAttribute("data-export-fallback-test", "");
    const image = document.createElementNS(ns, "image");
    image.setAttribute("data-technology-logo", "export-failure");
    image.setAttribute("href", "https://example.invalid/unavailable-logo.png");
    const label = document.createElementNS(ns, "text");
    label.setAttribute("data-technology-fallback", "export-failure");
    label.setAttribute("visibility", "hidden");
    label.textContent = "Unavailable logo";
    group.append(image, label);
    svg.append(group);
    const serialize = XMLSerializer.prototype.serializeToString;
    window.__restoreCardSerializer = () => {
      XMLSerializer.prototype.serializeToString = serialize;
    };
    XMLSerializer.prototype.serializeToString = function (node) {
      const fixture = node.querySelector?.("[data-export-fallback-test]");
      if (fixture) {
        window.__cardExportFallback =
          !fixture.querySelector("image") &&
          fixture.querySelector("text").getAttribute("visibility") === "visible";
      }
      return serialize.call(this, node);
    };
  });
  const downloadPromise = page.waitForEvent("download");
  await preview.getByRole("button", { name: "Download card", exact: true }).click();
  const download = await downloadPromise;
  assert.match(download.suggestedFilename(), /^devfeed-.*\.png$/);
  assert.equal(await download.failure(), null);
  assert.equal(
    await page.evaluate(() => {
      window.__restoreCardSerializer();
      document.querySelector("[data-export-fallback-test]")?.remove();
      return window.__cardExportFallback;
    }),
    true,
    "Failed logo exports show the topic name instead of an empty badge",
  );
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
    // Find a solid dot in the scattered artwork instead of assuming its position.
    const dot = [...document.querySelectorAll("svg.dev-card-artwork .dev-card-dot")].find(
      (node) => Number(node.getAttribute("opacity")) === 1 && Number(node.getAttribute("x")) > 280,
    );
    if (!dot) return false;
    const theme = getComputedStyle(document.documentElement);
    return [
      [theme.getPropertyValue("--card").trim(), 20, 680],
      [
        getComputedStyle(dot).fill,
        (Number(dot.getAttribute("x")) + 7) * 2,
        (Number(dot.getAttribute("y")) + 7) * 2,
      ],
    ].every(([expected, x, y]) => {
      const pixel = [...ctx.getImageData(x, y, 1, 1).data];
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
  const statLabels = await preview
    .locator(".dev-card-stats > g > text:last-child")
    .evaluateAll((nodes) =>
      nodes.map((node) => {
        const scale = node.ownerSVGElement.getBoundingClientRect().width / 560;
        const box = node.getBBox();
        const group = node.parentElement.getBoundingClientRect();
        const footer = node.ownerSVGElement
          .querySelector(".dev-card-brand")
          .getBoundingClientRect();
        return {
          fontSize: parseFloat(getComputedStyle(node).fontSize) * scale,
          width: box.width,
          aboveFooter: group.bottom < footer.top,
        };
      }),
    );
  assert.equal(statLabels.length, 3);
  for (const label of statLabels) {
    assert.ok(label.fontSize >= 11, "Streak labels remain readable on narrow cards");
    assert.ok(label.width <= 149, "Streak labels fit their columns within font rounding");
    assert.ok(label.aboveFooter, "Streak text stays clear of the card footer");
  }
  const downloadBox = await preview
    .getByRole("button", { name: "Download card", exact: true })
    .boundingBox();
  assert.ok(artwork.y + artwork.height < downloadBox.y, "Actions must not overlap the card");
  await page.setViewportSize(viewport);
  for (const theme of ["Terminal", "Aurora", "Minimal", "Classic"]) {
    await page.getByRole("radio", { name: new RegExp(`^${theme}`) }).check();
    await page.getByRole("radio", { name: "Rose", exact: true }).check();
    await page.getByRole("radio", { name: "Animated", exact: true }).check();
    const artwork = preview.locator("svg.dev-card-artwork");
    await page.waitForFunction(() =>
      document
        .querySelector("svg.dev-card-artwork")
        ?.getAnimations({ subtree: true })
        .some((animation) => animation.currentTime > 30),
    );
    const motionState = () =>
      artwork.evaluate((svg) => {
        const selector = {
          classic: ".dev-card-dot",
          terminal: ".dev-card-signal",
          aurora: ".dev-card-wave",
          minimal: ".dev-card-ripple",
        }[svg.dataset.cardTheme];
        return [...svg.querySelectorAll(selector)].map((node) => {
          const style = getComputedStyle(node);
          return {
            transform: style.transform,
            opacity: style.opacity,
            dash: style.strokeDashoffset,
            duration: style.animationDuration,
            delay: style.animationDelay,
            x: node.getAttribute("x"),
            y: node.getAttribute("y"),
          };
        });
      });
    const before = await motionState();
    const firstFrame = await artwork.screenshot({ animations: "allow" });
    await page.waitForTimeout(400);
    const after = await motionState();
    assert.notDeepEqual(after, before, `${theme} has visible theme-specific motion`);
    assert.equal(
      firstFrame.equals(await artwork.screenshot({ animations: "allow" })),
      false,
      `${theme} visibly changes between rendered frames`,
    );
    if (theme === "Classic") {
      assert.equal(before.length, 275);
      assert.ok(
        new Set(before.map(({ duration }) => duration)).size > 200,
        "Dots have varied cycle lengths",
      );
      assert.ok(
        new Set(before.map(({ delay }) => delay)).size > 200,
        "Dots have scattered start times",
      );
      assert.ok(
        after.some(({ opacity }) => Number(opacity) === 0),
        "Some dots disappear completely",
      );
      assert.ok(
        after.some(({ opacity }) => Number(opacity) > 0.5),
        "Other dots are visible at the same time",
      );
      assert.deepEqual(
        after.map(({ x, y }) => ({ x, y })),
        before.map(({ x, y }) => ({ x, y })),
        "Classic dot positions stay fixed",
      );
      assert.ok(after.every(({ transform }) => transform === "none"));
      assert.equal(
        await artwork
          .locator(".dev-card-motion-layer")
          .evaluate((node) => getComputedStyle(node).transform),
        "none",
        "Classic background stays fixed",
      );
      assert.ok(
        new Set(after.map(({ opacity }) => opacity)).size > 2,
        "Dots fade independently across the canvas",
      );
    }
    await page.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(await artwork.evaluate((svg) => svg.getAnimations({ subtree: true }).length), 0);
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.getByRole("radio", { name: "Static", exact: true }).check();
    assert.equal(await artwork.evaluate((svg) => svg.getAnimations({ subtree: true }).length), 0);
    await page.getByRole("radio", { name: "Animated", exact: true }).check();

    assert.equal(
      await preview.locator("svg.dev-card-artwork").getAttribute("data-card-theme"),
      theme.toLowerCase(),
    );
    assert.equal(await preview.getByRole("button", { name: "Download card" }).isDisabled(), true);
    await page.getByRole("button", { name: "Save changes", exact: true }).click();
    await preview.getByRole("button", { name: "Download card" }).waitFor();
    await page.getByText("Your profile is saved.", { exact: true }).waitFor();
    assert.equal(
      await preview.locator("svg.dev-card-artwork").getAttribute("data-card-theme"),
      theme.toLowerCase(),
    );
    assert.equal(
      await preview.locator("svg.dev-card-artwork").getAttribute("data-card-accent"),
      "rose",
    );
    await page.evaluate(() => {
      const serialize = XMLSerializer.prototype.serializeToString;
      window.__restoreMotionSerializer = () => {
        XMLSerializer.prototype.serializeToString = serialize;
      };
      XMLSerializer.prototype.serializeToString = function (node) {
        window.__cardExportMotion = {
          motion: node.getAttribute("data-card-motion"),
          styles: node.querySelectorAll("style[data-card-motion-style]").length,
        };
        return serialize.call(this, node);
      };
    });
    const download = page.waitForEvent("download");
    await preview.getByRole("button", { name: "Download card" }).click();
    const exportedCard = await download;
    assert.ok((await readFile(await exportedCard.path())).length > 1000);
    assert.deepEqual(await page.evaluate(() => window.__cardExportMotion), {
      motion: "static",
      styles: 0,
    });
    await page.evaluate(() => window.__restoreMotionSerializer());
    assert.equal(await artwork.getAttribute("data-card-motion"), "animated");
    await exportedCard.saveAs(`${prefix}-${theme.toLowerCase()}-export.png`);
    await page.setViewportSize({ width: 390, height: 844 });
    await preview.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${prefix}-${theme.toLowerCase()}-mobile.png` });
    await page.setViewportSize(viewport);
  }
  await page.getByRole("radio", { name: "Static", exact: true }).check();
  await page.getByRole("radio", { name: "Theme default", exact: true }).check();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.getByText("Your profile is saved.", { exact: true }).waitFor();
}

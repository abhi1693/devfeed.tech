import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

export async function checkDevCard(page, prefix, expectedMotion = "animated") {
  const preview = page.getByRole("complementary", { name: "Dev card preview", exact: true });
  assert.equal(
    await preview.getByRole("radio", { name: "Dev Card", exact: true }).isChecked(),
    true,
  );
  assert.equal(await preview.getByRole("button", { name: "Download X header" }).count(), 0);
  assert.equal(await preview.getByRole("button", { name: "Copy Link", exact: true }).count(), 0);
  const publicProfile = preview.getByRole("link", { name: "View public profile", exact: true });
  if (await publicProfile.count()) {
    assert.equal(
      await publicProfile.evaluate((link) =>
        link.previousElementSibling?.classList.contains("dev-card-download"),
      ),
      true,
      "Public profile action sits beside the download action",
    );
  }
  assert.equal(
    await page
      .getByRole("radio", {
        name: expectedMotion === "animated" ? "Animated" : "Static",
        exact: true,
      })
      .isChecked(),
    true,
  );
  assert.equal(
    await preview.locator("svg.dev-card-artwork").getAttribute("data-card-motion"),
    expectedMotion,
  );
  const name = page.getByRole("textbox", { name: "Display name", exact: true });
  const originalName = await name.inputValue();
  const motionHelp = page.getByRole("button", { name: "About card motion", exact: true });
  const tooltip = page.getByRole("tooltip");
  assert.equal(await tooltip.count(), 0);
  await motionHelp.hover();
  await tooltip.waitFor();
  assert.match(await tooltip.textContent(), /Image downloads stay static/);
  await page.keyboard.press("Escape");
  await tooltip.waitFor({ state: "hidden" });
  await page.mouse.move(0, 0);
  await motionHelp.evaluate((node) => {
    node.blur();
    node.focus({ preventScroll: true });
  });
  await tooltip.waitFor();
  await page.keyboard.press("Escape");
  await tooltip.waitFor({ state: "hidden" });
  await name.focus();
  const customizer = page.locator(".dev-card-customizer");
  const editorViewport = page.viewportSize();
  const previousTheme = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (value) => document.documentElement.classList.toggle("dark", value === "dark"),
      theme,
    );
    await customizer.screenshot({
      path: `${prefix}-editor-${theme}.png`,
      animations: "allow",
      style:
        "header, .mobile-nav { visibility: hidden !important; } * { transition: none !important; }",
    });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await customizer.screenshot({
    path: `${prefix}-editor-mobile.png`,
    animations: "allow",
    style:
      "header, .mobile-nav { visibility: hidden !important; } * { transition: none !important; }",
  });
  assert.equal(await customizer.evaluate((node) => node.scrollWidth <= node.clientWidth), true);
  await page.setViewportSize(editorViewport);
  await page.evaluate((value) => {
    document.documentElement.classList.toggle("dark", value);
  }, previousTheme);
  await page.getByRole("radio", { name: "Classic", exact: true }).focus();
  await page.keyboard.down("ArrowRight");
  await page.waitForFunction(
    () =>
      document
        .querySelector('.dev-card-theme-options [aria-label="Terminal"]')
        ?.getAttribute("aria-checked") === "true",
  );
  await page.keyboard.up("ArrowRight");
  assert.equal(await page.getByRole("radio", { name: "Terminal", exact: true }).isChecked(), true);
  await page.keyboard.down("ArrowLeft");
  await page.waitForFunction(
    () =>
      document
        .querySelector('.dev-card-theme-options [aria-label="Classic"]')
        ?.getAttribute("aria-checked") === "true",
  );
  await page.keyboard.up("ArrowLeft");
  assert.equal(await page.getByRole("radio", { name: "Classic", exact: true }).isChecked(), true);
  await page.evaluate(() => {
    const original = SVGTextContentElement.prototype.getComputedTextLength;
    window.__cardTextMeasurements = 0;
    SVGTextContentElement.prototype.getComputedTextLength = function () {
      window.__cardTextMeasurements++;
      return original.call(this);
    };
    window.__restoreCardTextMeasurements = () => {
      SVGTextContentElement.prototype.getComputedTextLength = original;
    };
  });
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
  assert.equal(await preview.getByRole("heading").count(), 0);
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
    const card = await preview.locator(".dev-card-artwork").boundingBox();
    assert.ok(
      bounds.y + bounds.height < card.y + card.height - 12,
      "Full bio stays inside the card",
    );
  }
  const measurements = await page.evaluate(() => {
    window.__restoreCardTextMeasurements();
    return window.__cardTextMeasurements;
  });
  assert.equal(measurements, 0, "Editing card text must not trigger synchronous SVG measurements");
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
  assert.equal(await preview.locator(".dev-card-brand text").getAttribute("font-size"), "20");
  const brandPosition = await preview.locator(".dev-card-brand").evaluate((brand) => {
    const box = brand.getBBox();
    const text = brand.querySelector("text").getBBox();
    const mark = brand.querySelector("image").getBBox();
    return {
      connectedToEdge: box.x >= 280 && box.x + box.width === 540 && box.y === 20,
      centered: Math.abs((mark.x + text.x + text.width) / 2 - 470) < 4,
      gap: text.x - (mark.x + mark.width),
      matchesCard:
        getComputedStyle(brand.querySelector("path")).fill ===
        getComputedStyle(brand.ownerSVGElement.querySelector("g > rect")).fill,
    };
  });
  assert.ok(brandPosition.connectedToEdge, "Brand tab joins the artwork's top and right edges");
  assert.ok(brandPosition.matchesCard, "Brand tab blends into the card surface");
  assert.ok(brandPosition.centered, "Logo and wordmark are centered inside the corner tab");
  assert.ok(brandPosition.gap >= 0 && brandPosition.gap <= 4, "Brand lockup stays compact");
  assert.equal(
    (await preview.locator(".dev-card-bio text:not([aria-hidden])").allTextContents())
      .join("")
      .replace(/\s/gu, ""),
    originalBio.replace(/\s/gu, ""),
  );
  assert.equal(
    await preview.locator(".dev-card-stats > rect, .dev-card-stats > g > rect").count(),
    0,
  );
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
    // Sample a solid dot below the brand tab and to the right of the avatar.
    const dot = [...document.querySelectorAll("svg.dev-card-artwork .dev-card-dot")].find(
      (node) =>
        Number(node.getAttribute("opacity")) === 1 &&
        Number(node.getAttribute("x")) > 280 &&
        Number(node.getAttribute("y")) >= 90,
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
  const hasWordmark = await page.evaluate(async (base64) => {
    const image = new Image();
    image.src = `data:image/png;base64,${base64}`;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0);
    const brand = document.querySelector(".dev-card-preview .dev-card-brand");
    const box = brand.querySelector("path").getBBox();
    const pixels = ctx.getImageData(box.x * 2, box.y * 2, box.width * 2, box.height * 2).data;
    ctx.fillStyle = getComputedStyle(brand.querySelector("text")).fill;
    ctx.fillRect(0, 0, 1, 1);
    const ink = ctx.getImageData(0, 0, 1, 1).data;
    let painted = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (
        Math.abs(pixels[i] - ink[0]) +
          Math.abs(pixels[i + 1] - ink[1]) +
          Math.abs(pixels[i + 2] - ink[2]) <
        30
      )
        painted++;
    }
    return painted > 100;
  }, bytes.toString("base64"));
  assert.equal(hasWordmark, true, "The wordmark is visible in the downloaded PNG");
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
        const card = node.ownerSVGElement.getBoundingClientRect();
        return {
          fontSize: parseFloat(getComputedStyle(node).fontSize) * scale,
          width: box.width,
          bottomPadding: card.bottom - group.bottom,
        };
      }),
    );
  assert.equal(statLabels.length, 3);
  const statIcons = await preview.locator("[data-stat-icon]").evaluateAll((nodes) =>
    nodes.map((node) => {
      const icon = node.getBoundingClientRect();
      const value = node.nextElementSibling.getBoundingClientRect();
      const label = node.nextElementSibling.nextElementSibling.getBoundingClientRect();
      const svg = node.ownerSVGElement;
      const scale = svg.getBoundingClientRect().width / 560;
      const column = node.parentElement.getCTM().e;
      const expectedCenter =
        svg.getBoundingClientRect().left + column + (240 / nodes.length) * scale;
      return {
        width:
          (Number(node.getAttribute("width")) *
            node.ownerSVGElement.getBoundingClientRect().width) /
          560,
        clearOfValue: icon.right + 3 <= value.left,
        besideValue: icon.top < value.bottom && icon.bottom > value.top,
        pairCenterOffset: Math.abs((icon.left + value.right) / 2 - (label.left + label.right) / 2),
        columnCenterOffset: Math.abs((label.left + label.right) / 2 - expectedCenter),
        hidden: node.getAttribute("aria-hidden"),
      };
    }),
  );
  assert.equal(statIcons.length, 3);
  for (const icon of statIcons) {
    assert.ok(
      icon.width >= 14 && icon.width <= 18,
      "Stat icons remain small but readable on mobile",
    );
    assert.ok(icon.clearOfValue, "Stat icons have breathing room beside the numbers");
    assert.ok(icon.besideValue, "Stat icons align with the numbers");
    assert.ok(icon.pairCenterOffset <= 2, "Each icon and number pair is centered over its label");
    assert.ok(icon.columnCenterOffset <= 1, "Stat labels are centered in equal card columns");
    assert.equal(icon.hidden, "true");
  }
  for (const label of statLabels) {
    assert.ok(label.fontSize >= 11, "Streak labels remain readable on narrow cards");
    assert.ok(label.width <= 149, "Streak labels fit their columns within font rounding");
    assert.ok(
      label.bottomPadding >= 16 && label.bottomPadding <= 28,
      "Stats have balanced bottom padding",
    );
  }
  const downloadBox = await preview
    .getByRole("button", { name: "Download card", exact: true })
    .boundingBox();
  assert.ok(artwork.y + artwork.height < downloadBox.y, "Actions must not overlap the card");
  for (const dark of [false, true]) {
    await page.locator("html").evaluate((node, dark) => node.classList.toggle("dark", dark), dark);
    await preview.locator("svg.dev-card-artwork").screenshot({
      path: `${prefix}-card-${dark ? "dark" : "light"}-mobile.png`,
      animations: "allow",
      style:
        "header, .mobile-nav { visibility: hidden !important; } * { transition: none !important; }",
    });
  }
  await page.locator("html").evaluate((node, dark) => node.classList.toggle("dark", dark), wasDark);
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
    assert.equal(await preview.getByRole("button", { name: "Download X header" }).count(), 0);
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
    const cardPalette = await artwork.evaluate((svg) =>
      ["--card", "--chart-1"].map((token) => getComputedStyle(svg).getPropertyValue(token)),
    );
    const cardView = preview.getByRole("radio", { name: "Dev Card", exact: true });
    await cardView.focus();
    await page.keyboard.down("ArrowRight");
    await page.waitForFunction(
      () =>
        document.querySelector('.dev-card-preview-options [aria-checked="true"]')?.textContent ===
        "Header",
    );
    await page.keyboard.up("ArrowRight");
    assert.equal(await preview.getByRole("button", { name: "Download card" }).count(), 0);
    assert.equal(await page.getByRole("radio", { name: "Animated", exact: true }).count(), 0);
    assert.equal(await page.getByRole("radio", { name: "Static", exact: true }).count(), 0);
    const header = preview.locator("svg.dev-card-x-header");
    assert.deepEqual(
      await header.evaluate((svg) =>
        ["--card", "--chart-1"].map((token) => getComputedStyle(svg).getPropertyValue(token)),
      ),
      cardPalette,
    );
    assert.equal(await header.locator("image[data-avatar]").count(), 0);
    assert.ok(await header.getAttribute("aria-describedby"));
    assert.equal(await header.getAttribute("data-card-theme"), theme.toLowerCase());
    assert.equal(await header.getAttribute("data-card-accent"), "rose");
    assert.equal(await header.getAttribute("data-card-motion"), "static");
    assert.equal(await header.evaluate((svg) => svg.getAnimations({ subtree: true }).length), 0);
    const contrastRatios = await header.evaluate((svg) => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const context = canvas.getContext("2d");
      const luminance = (color) => {
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        const values = [...context.getImageData(0, 0, 1, 1).data]
          .slice(0, 3)
          .map((byte) => byte / 255)
          .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4));
        return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
      };
      const background = getComputedStyle(svg.querySelector(":scope > rect")).fill;
      return [
        ...svg.querySelectorAll("[data-header-content] text, .dev-card-header-brand text"),
      ].map((node) => {
        const tile =
          node.closest("[data-header-technologies]") && node.parentElement.querySelector("rect");
        const values = [
          luminance(getComputedStyle(node).fill),
          luminance(tile ? getComputedStyle(tile).fill : background),
        ].sort((a, b) => b - a);
        return (values[0] + 0.05) / (values[1] + 0.05);
      });
    });
    assert.ok(
      contrastRatios.every((ratio) => ratio >= 4.5),
      "Every header label meets 4.5:1 contrast",
    );
    const contentPosition = await header.evaluate((svg) => {
      const frame = svg.getBoundingClientRect();
      const box = svg.querySelector("[data-header-content]").getBoundingClientRect();
      const scale = frame.width / 1500;
      return {
        center: (box.y + box.height / 2 - frame.y) / scale,
        top: (box.y - frame.y) / scale,
        bottom: (box.bottom - frame.y) / scale,
      };
    });
    assert.ok(
      // Scaled font metrics round to device pixels; allow under one preview pixel.
      Math.abs(contentPosition.center - 270) < 2,
      `Header centers its actual content vertically: ${JSON.stringify(contentPosition)}`,
    );
    assert.ok(
      contentPosition.top >= 111 && contentPosition.bottom <= 435,
      "Header content stays clear of branding and crop edges",
    );
    assert.equal(
      await header.locator("[data-header-content] text").evaluateAll((nodes) =>
        nodes
          .filter((node) => node.textContent.trim())
          .every((node) => {
            const box = node.getBBox();
            const transform = node.ownerSVGElement.getCTM().inverse().multiply(node.getCTM());
            const left = new DOMPoint(box.x, box.y).matrixTransform(transform);
            const right = new DOMPoint(box.x + box.width, box.y + box.height).matrixTransform(
              transform,
            );
            return left.x >= 549 && right.x <= 1400 && left.y >= 70 && right.y <= 435;
          }),
      ),
      true,
      "Header content stays readable within its available width",
    );
    const headerDownload = page.waitForEvent("download");
    await preview.getByRole("button", { name: "Download X header", exact: true }).click();
    const exportedHeader = await headerDownload;
    assert.match(exportedHeader.suggestedFilename(), /^devfeed-.+-x-header\.png$/);
    const headerBytes = await readFile(await exportedHeader.path());
    assert.equal(headerBytes.subarray(1, 4).toString(), "PNG");
    assert.equal(headerBytes.readUInt32BE(16), 1500);
    assert.equal(headerBytes.readUInt32BE(20), 500);
    assert.ok(headerBytes.length > 20000, "The header contains rendered artwork");
    await exportedHeader.saveAs(`${prefix}-${theme.toLowerCase()}-x-header.png`);
    await preview.getByText("Your X header is downloaded.", { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await preview.screenshot({
      path: `${prefix}-${theme.toLowerCase()}-header-preview.png`,
      style: "header, .mobile-nav { visibility: hidden !important; }",
    });
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await preview.getByRole("radio", { name: "Dev Card", exact: true }).check();
    assert.equal(
      await page.getByRole("radio", { name: "Animated", exact: true }).isChecked(),
      true,
    );
    assert.equal(await artwork.getAttribute("data-card-motion"), "animated");
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

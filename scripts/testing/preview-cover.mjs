import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import sharp from "sharp";
import UnsizedImages from "lighthouse/core/audits/unsized-images.js";

// Same deterministic delayed-image journey for the website and both built extensions.
export async function checkPreviewCover(context, href, output) {
  const baseline = process.env.DEVFEED_COVER_BASELINE === "1";
  output += baseline ? "/before" : "/after";
  await mkdir(output, { recursive: true });
  const results = [];
  for (const viewport of [
    { width: 412, height: 823 },
    { width: 1350, height: 940 },
  ]) {
    for (const [name, width, height] of [
      ["landscape", 960, 400],
      ["square", 600, 600],
      ["portrait", 400, 800],
      ["failed", 960, 400],
    ]) {
      const page = await context.newPage();
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      const body = await sharp({ create: { width, height, channels: 3, background: "#659ab5" } })
        .png()
        .toBuffer();
      await page.setViewportSize(viewport);
      // Measure image-induced geometry, without the dialog entrance transform.
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.addInitScript(() => {
        window.coverLayoutShifts = [];
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries())
            if (!entry.hadRecentInput) window.coverLayoutShifts.push(entry.value);
        }).observe({ type: "layout-shift", buffered: true });
      });
      await page.route("https://images.example.test/**", async (route) => {
        await gate;
        if (name === "failed") await route.abort();
        else await route.fulfill({ contentType: "image/png", body });
      });
      try {
        await page.goto(href, { waitUntil: "domcontentloaded" });
        const cover = page.locator(".preview-cover");
        const image = cover.locator("img");
        await page.locator("#article-preview-title").waitFor();
        await image.waitFor({ state: "attached" });
        await page.evaluate(() => document.fonts.ready);
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const measure = () =>
          page.evaluate(() => {
            const box = (selector) => {
              const rect = document.querySelector(selector).getBoundingClientRect();
              return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            };
            return {
              cover: box(".preview-cover"),
              copy: box(".preview-copy"),
              footer: box(".preview-footer"),
            };
          });
        const before = await measure();
        const sizing = await image.evaluate((img) => {
          // Use authored sizing declarations, not computed pixel heights derived after loading.
          const declarations = {};
          const visit = (rules) => {
            for (const rule of rules) {
              if (rule.selectorText && img.matches(rule.selectorText)) {
                for (const property of ["width", "height", "aspect-ratio"])
                  if (rule.style.getPropertyValue(property))
                    declarations[property] = rule.style.getPropertyValue(property);
              }
              if (rule.cssRules) visit(rule.cssRules);
            }
          };
          for (const sheet of document.styleSheets) visit(sheet.cssRules);
          return {
            attributeWidth: img.getAttribute("width"),
            attributeHeight: img.getAttribute("height"),
            cssEffectiveRules: {
              width: declarations.width,
              height: declarations.height,
              aspectRatio: declarations["aspect-ratio"],
            },
          };
        });
        const sized = UnsizedImages.isSizedImage(sizing);
        await page.screenshot({ path: `${output}/${viewport.width}-${name}-loading.png` });
        release();
        if (name === "failed") await cover.locator(".image-placeholder").waitFor();
        else await image.evaluate((img) => img.decode());
        await page.evaluate(
          () =>
            new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const after = await measure();
        const rendering =
          name === "failed"
            ? null
            : await image.evaluate((img) => ({
                naturalWidth: img.naturalWidth,
                naturalHeight: img.naturalHeight,
                objectFit: getComputedStyle(img).objectFit,
              }));
        const layoutShifts = await page.evaluate(() => window.coverLayoutShifts);
        await page.screenshot({ path: `${output}/${viewport.width}-${name}-settled.png` });
        results.push({ viewport, name, before, after, rendering, sized, sizing, layoutShifts });
        if (!baseline) {
          assert.ok(before.cover.height > 0, "Cover reserves space while the image is blocked");
          assert.ok(sized, "Lighthouse unsized-images recognizes explicit cover sizing");
          for (const selector of ["cover", "copy", "footer"])
            for (const dimension of ["x", "y", "width", "height"])
              assert.ok(
                Math.abs(before[selector][dimension] - after[selector][dimension]) < 1,
                `${name}: ${selector}.${dimension} stays stable`,
              );
          if (rendering) {
            assert.equal(
              rendering.objectFit,
              "contain",
              "Show the entire image without crop or distortion",
            );
            // Width-descriptor srcsets density-correct and round natural dimensions.
            assert.ok(
              Math.abs(rendering.naturalWidth / rendering.naturalHeight / (width / height) - 1) <
                0.01,
            );
          }
        }
      } finally {
        release();
        await page.close();
        await writeFile(`${output}/report.json`, JSON.stringify({ baseline, results }, null, 2));
      }
    }
  }
  console.log(
    `Preview covers: ${results.length} delayed/failed image scenarios recorded (${baseline ? "before" : "after"}).`,
  );
}

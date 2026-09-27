import { chromium } from "playwright";
import { format } from "prettier";
import { writeFile } from "node:fs/promises";

// Run on Linux with Liberation Sans (Arial-compatible) installed. These checked-in
// advances need no font download or DOM measurement at runtime. SVG text disables
// kerning so the same advances apply on the server and in browser previews.
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const metrics = await page.evaluate(() => {
    const context = document.createElement("canvas").getContext("2d");
    context.fontKerning = "none";
    const glyphs = {};
    for (const [start, end] of [
      [32, 383],
      [0x2010, 0x2027],
      [0x2030, 0x203a],
      [0x20ac, 0x20ac],
    ]) {
      for (let code = start; code <= end; code++) {
        if (code >= 127 && code < 160) continue;
        const char = String.fromCodePoint(code);
        glyphs[char] = [400, 700].map((weight) => {
          context.font = `${weight} 1000px Arial`;
          return Math.round(context.measureText(char).width * 1000) / 1000;
        });
      }
    }
    return glyphs;
  });
  await writeFile(
    new URL("../../apps/web/src/lib/dev-card-font-metrics.json", import.meta.url),
    await format(JSON.stringify(metrics), { parser: "json", printWidth: 100 }),
  );
} finally {
  await browser.close();
}

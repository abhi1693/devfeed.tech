import { renderToStaticMarkup } from "react-dom/server";
import { Eye, Plus } from "lucide-react";
import sharp from "sharp";
import { expect, it } from "vitest";
import { CardEye, CardPlus } from "@/components/reader-card-icons";

it.each([
  ["eye", Eye, CardEye],
  ["plus", Plus, CardPlus],
] as const)(
  "preserves the %s icon's Lucide rendering with fewer SVG elements",
  async (_, Original, Compact) => {
    for (const size of [15, 16, 32]) {
      for (const strokeWidth of [1.8, 2]) {
        const original = renderToStaticMarkup(<Original size={size} strokeWidth={strokeWidth} />);
        const compact = renderToStaticMarkup(<Compact size={size} strokeWidth={strokeWidth} />);
        expect((compact.match(/<(path|circle|line)\b/g) ?? []).length).toBeLessThan(
          (original.match(/<(path|circle|line)\b/g) ?? []).length,
        );
        const raster = (svg: string) => sharp(Buffer.from(svg)).ensureAlpha().raw().toBuffer();
        const [a, b] = await Promise.all([raster(original), raster(compact)]);
        expect(a.length).toBe(b.length);
        // Equivalent SVG circles and arc subpaths can differ slightly in edge antialiasing.
        const meanError =
          a.reduce((sum, value, index) => sum + Math.abs(value - b[index]), 0) / a.length;
        expect(meanError).toBeLessThan(0.2);
      }
    }
  },
);

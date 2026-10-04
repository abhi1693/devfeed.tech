# Dev Card text layout

Reader previews, Chrome/Edge cards, and server SVG embeds share `apps/web/src/lib/dev-card-text.ts`
and `apps/web/src/components/dev-card-text.tsx`. Keep wrapping, fitting, and stat positioning in
these shared modules. Names use at most two lines; bios keep all text, including unbroken words
and Unicode graphemes. Fitting uses bounded searches and a bounded cache of font advances.

`dev-card-font-metrics.json` contains regular/bold Arial-compatible advances at 1000 units.
Regenerate it with `node scripts/assets/dev-card-font-metrics.mjs` on Linux with Liberation Sans
installed. The generator uses Playwright; production rendering needs neither a browser measurement
nor a font download. Card text disables kerning to match the additive advances. Unlisted glyphs
use conservative widths; actual glyph appearance still depends on the viewer's system fonts.
If the theme's Arial/Helvetica font family changes, update the metrics and validate the web app,
standalone SVG/PNG exports, and both extensions together.

## Browser icons

Run `node scripts/assets/browser-icons.mjs` to regenerate the compact PNG variants
from `packages/theme/assets/devfeed-mark.png`: 32 pixels for browser favicons,
128 pixels for the Chrome/Edge package and reader mark, and 180 pixels for Apple
touch icons. Commit the generated assets with changes to the original mark.
Web/admin metadata and extension builds use these precomputed assets so icon
requests need no image transformation at runtime.

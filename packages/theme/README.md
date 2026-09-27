# DevFeed theme

The admin and public user apps share this workspace package. `tokens.css` owns the
admin's neutral light and dark palettes, semantic surface/text/action/border colors,
font family, corner radii and overlay shadows. `.dark` on the document root selects
the dark palette; the default is light.

Both apps import `@devfeed/theme/tokens.css`. The admin also imports
`@devfeed/theme/tailwind.css`, which maps the same tokens to its existing Tailwind
utilities. The public app uses the tokens directly in its component styles. There
is no separate public palette or profile-page palette.

Use `--muted` for a surface and `--muted-foreground` for secondary text. Use
`--primary`/`--primary-foreground` for primary actions, `--accent` for subtle hover
or selected surfaces, and `--popover`/`--popover-foreground` for menus. Layout,
component behavior and saved theme preferences remain owned by each app.

After changing theme tokens, run both apps' lint, tests and production builds, then
check light/dark desktop and mobile screens, including menus, previews and settings.
Both frontend Dockerfiles include this package, and Compose watch tracks its files.

`assets/` owns the original public-app logo files. Both apps statically import the
mark for their headers and browser icons. `brand.css` shares the logo sizing,
wordmark typography and dark-theme treatment; the supplied raster wordmark remains
available for light-background artwork. See `docs/brand-assets.md` in the repo root.

`dev-card.ts` owns the optional Dev Card theme palettes and accent colors. These
are scoped to card artwork and shared with image exports; Classic inherits the
reader palette. Saved Terminal, Aurora, and Minimal designs have fixed palettes
so they retain their appearance when embedded elsewhere.

`dev-card-motion.ts` provides the embedded SVG animation stylesheet, scoped to
animated cards and disabled for reduced-motion preferences. Text, photos, and
statistics stay still. Raster exports intentionally omit these animation rules.
Classic keeps its background fixed while scattered colored dots fade in and out
with independent, seeded timings. Static cards use a scattered still composition.
Terminal sends staggered signals along fixed circuit tracks, Aurora moves each
wave independently, and Minimal expands and fades concentric ripples.

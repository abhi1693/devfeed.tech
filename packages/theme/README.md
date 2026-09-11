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

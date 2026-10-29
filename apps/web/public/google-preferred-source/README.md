# Google preferred-source badge artwork

The lossless WebP files encode Google's official English light/dark badges from the
2× artwork in its
[all-language badge archive](https://services.google.com/fh/files/helpcenter/google_preferred_source_badge_all_languages.zip).
The archive is linked from the
[Google Search Central preferred-sources guide](https://developers.google.com/search/docs/appearance/preferred-sources).
Both images remain 676 × 213 pixels with identical decoded RGBA values, including
alpha and the RGB values of fully transparent pixels. The artwork has not been
redrawn, recolored or resized; CSS controls its displayed size.

| Local file | Original archive entry | Original PNG bytes | WebP bytes | Reduction |
| --- | --- | ---: | ---: | ---: |
| `light.webp` | `EN/google_preferred_source_badge_light_en@2x.png` | 25,219 | 13,540 | 46.31% |
| `dark.webp` | `EN/google_preferred_source_badge_dark_en@2x.png` | 25,611 | 13,950 | 45.53% |

Verified SHA-256 checksums on 2026-10-30:

```text
archive: f6829b8e76a42d026dce886a3f738f3969bd076ea0ac9684329dbdbec7d36442
original light.png: bb7427f3fde64461d0ba27b83b94889b9d723bfd0c0ece57d82bfda055a1547d
original dark.png: 3820c3c79c75ad6356f36de99d1eda119c64220044407aaf80c1f0ec6cabcb65
light.webp: ef0cca4e5a1931ed765b72a9ea62fe484b751ee939671f4c0570ca403e9a1f20
dark.webp: 4792f47bc9f23a5bd85e54731d9b3bcafe6ae8c59f9e1d514d5f4bfab7582633
```

Encoding used Sharp 0.35.5, libvips 8.18.7 and libwebp 1.6.0 with `lossless: true`,
`nearLossless: false`, `exact: true`, `quality: 100`, `alphaQuality: 100` and
`effort: 6`. Both output containers contain a lossless `VP8L` image and no lossy
`VP8 ` image. Both source and output images use sRGB without an embedded ICC
profile; the encoder preserves an existing ICC profile if present.

Each source and output decodes to 575,952 matching RGBA bytes. Their decoded hashes
are:

```text
light RGBA: 7791a1ef3e43e87c475d86a1b6bbf1f6133b16514d95c534da78a41cfc6a1433
dark RGBA: bba59b7d8ce4bfc29695090fd166f55de25944633c3cb7c51ceff8c2bfb10ecb
```

To reproduce from the repository root, extract the original archive, verify its
hash and the two PNG hashes above, then provide its `EN` directory:

```sh
node - /path/to/extracted/EN <<'JS'
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const sharp = require('sharp');
(async () => {
  for (const theme of ['light', 'dark']) {
    const source = await fs.readFile(path.join(process.argv[2],
      `google_preferred_source_badge_${theme}_en@2x.png`));
    const output = `apps/web/public/google-preferred-source/${theme}.webp`;
    await sharp(source).keepIccProfile().webp({
      lossless: true, nearLossless: false, exact: true,
      quality: 100, alphaQuality: 100, effort: 6,
    }).toFile(output);
    const before = await sharp(source).ensureAlpha().raw()
      .toBuffer({ resolveWithObject: true });
    const after = await sharp(output).ensureAlpha().raw()
      .toBuffer({ resolveWithObject: true });
    assert.equal(after.info.width, before.info.width);
    assert.equal(after.info.height, before.info.height);
    assert.equal(after.info.channels, 4);
    assert.ok(before.data.equals(after.data), 'Every RGBA byte must match');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
JS
```

Chrome and Edge packages include these local images and open Google's documented
preferred-source selection link. They do not load the remote publisher SDK.
The website uses the actual
[Google publisher SDK](https://news.google.com/swg/js/v1/publisher.js) in its isolated
widget. The downloaded archive and SDK reference copies are not shipped as
extension code. Update this provenance record when replacing the artwork.

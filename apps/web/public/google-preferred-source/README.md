# Google preferred-source badge artwork

The PNG files are Google's official English light/dark badges, copied byte-for-byte
from the 2× artwork in its
[all-language badge archive](https://services.google.com/fh/files/helpcenter/google_preferred_source_badge_all_languages.zip).
The archive is linked from the
[Google Search Central preferred-sources guide](https://developers.google.com/search/docs/appearance/preferred-sources).
Both images are 676 × 213 pixels. The artwork has not been redrawn, recolored or
resized; CSS controls its displayed size.

| Local file  | Original archive entry                             |
| ----------- | -------------------------------------------------- |
| `light.png` | `EN/google_preferred_source_badge_light_en@2x.png` |
| `dark.png`  | `EN/google_preferred_source_badge_dark_en@2x.png`  |

Verified SHA-256 checksums on 2026-10-29:

```text
archive: f6829b8e76a42d026dce886a3f738f3969bd076ea0ac9684329dbdbec7d36442
light.png: bb7427f3fde64461d0ba27b83b94889b9d723bfd0c0ece57d82bfda055a1547d
dark.png: 3820c3c79c75ad6356f36de99d1eda119c64220044407aaf80c1f0ec6cabcb65
```

Chrome and Edge packages include these local images and open Google's documented
preferred-source selection link. They do not load the remote publisher SDK.
The website uses the actual
[Google publisher SDK](https://news.google.com/swg/js/v1/publisher.js) in its isolated
widget instead of these PNG links. The downloaded archive and SDK reference copies
are not shipped as extension code.

To update the artwork, follow the archive link from Google's guide, extract the
English 2× light/dark PNGs without modifying their bytes, and verify the archive
entry names, dimensions and checksums before replacing these files. Update this
provenance record with the verified source and hashes.

import { fileURLToPath } from "node:url";
import sharp from "sharp";

const assets = fileURLToPath(new URL("../../packages/theme/assets/", import.meta.url));
for (const size of [32, 128, 180]) {
  await sharp(`${assets}devfeed-mark.png`)
    .resize(size, size)
    .png({ compressionLevel: 9, palette: true, colours: 128 })
    .toFile(`${assets}devfeed-icon-${size}.png`);
}

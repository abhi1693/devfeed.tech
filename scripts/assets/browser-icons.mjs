import { fileURLToPath, pathToFileURL } from "node:url";
import sharp from "sharp";

export async function generateBrowserIcons(
  assets = fileURLToPath(new URL("../../packages/theme/assets/", import.meta.url)),
) {
  for (const size of [32, 128, 180]) {
    await sharp(`${assets}/devfeed-mark.png`)
      .resize(size, size)
      .png({ compressionLevel: 9, palette: true, colours: 128 })
      .toFile(`${assets}/devfeed-icon-${size}.png`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await generateBrowserIcons();
}

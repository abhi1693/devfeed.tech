import { expect, it } from "vitest";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { buildExtension } from "../../extensions/build.mjs";

const extensions = fileURLToPath(new URL("../../extensions/dist/", import.meta.url));
it.each(["chrome", "edge"])("packages compact, loadable %s icons", async (browser) => {
  await buildExtension(browser);
  for (const [name, size, budget] of [
    ["icon.png", 128, 6144],
    ["favicon.png", 32, 2048],
  ] as const) {
    const image = await readFile(`${extensions}${browser}/${name}`);
    expect(await sharp(image).metadata()).toMatchObject({
      format: "png",
      width: size,
      height: size,
    });
    expect(image.length).toBeLessThanOrEqual(budget);
  }
  const document = await readFile(`${extensions}${browser}/newtab.html`, "utf8");
  expect(document).toContain('href="favicon.png"');
  expect(document).toContain('src="newtab.js"');
  const manifest = JSON.parse(await readFile(`${extensions}${browser}/manifest.json`, "utf8"));
  expect(manifest.icons[128]).toBe("icon.png");
  expect((await readFile(`${extensions}${browser}/newtab.js`)).length).toBeGreaterThan(0);
});

it("rejects unsupported browser packages", async () => {
  await expect(buildExtension("firefox")).rejects.toThrow("Unsupported browser: firefox");
});

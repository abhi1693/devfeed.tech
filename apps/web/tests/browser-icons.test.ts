import { afterEach, expect, it } from "vitest";
import { cp, mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { generateBrowserIcons } from "../../../scripts/assets/browser-icons.mjs";

const assets = fileURLToPath(new URL("../../../packages/theme/assets/", import.meta.url));
const directories: string[] = [];
afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true })));
});

it("generates reproducible PNGs within browser and extension transfer budgets", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "devfeed-icon-test-"));
  directories.push(directory);
  await cp(path.join(assets, "devfeed-mark.png"), path.join(directory, "devfeed-mark.png"));
  await generateBrowserIcons(directory);
  for (const [size, budget] of [
    [32, 2048],
    [128, 6144],
    [180, 10240],
  ]) {
    const name = `devfeed-icon-${size}.png`;
    const generated = await readFile(path.join(directory, name));
    expect(await sharp(generated).metadata()).toMatchObject({
      format: "png",
      width: size,
      height: size,
    });
    expect(generated.length).toBeLessThanOrEqual(budget);
    expect(generated).toEqual(await readFile(path.join(assets, name)));
  }
});

it("reports a missing original without creating placeholder icons", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "devfeed-icon-test-"));
  directories.push(directory);
  await expect(generateBrowserIcons(directory)).rejects.toThrow();
  await expect(readFile(path.join(directory, "devfeed-icon-32.png"))).rejects.toThrow();
});

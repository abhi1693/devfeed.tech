import { createHash } from "node:crypto";
import { mkdtemp, readFile, writeFile, rm, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it } from "vitest";
import { generateProfileIcons } from "../../../scripts/assets/profile-icons.mjs";
const directories: string[] = [];
afterEach(async () => {
  await Promise.all(
    directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })),
  );
});
it("generates decodable local SVGs with immutable content keys and an idempotent map", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "devfeed-profile-icons-"));
  directories.push(directory);
  const component = path.join(directory, "map.ts");
  const assets = path.join(directory, "assets");
  await writeFile(
    component,
    'const icons = { GitHub: "/profile-icons/github.000000000000.svg", GitLab: "/profile-icons/gitlab.000000000000.svg" };',
  );
  await generateProfileIcons(component, assets);
  const output = await readFile(component, "utf8");
  for (const filename of await readdir(assets)) {
    const svg = await readFile(path.join(assets, filename));
    expect(filename).toContain(createHash("sha256").update(svg).digest("hex").slice(0, 12));
    expect(output).toContain(`/profile-icons/${filename}`);
    expect(svg.toString()).toMatch(
      /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" viewBox="0 0 24 24"><path d="[^"]+"\/><\/svg>/,
    );
  }
  expect(await readdir(assets)).toHaveLength(2);
  await generateProfileIcons(component, assets);
  expect(await readFile(component, "utf8")).toBe(output);
});
it("fails clearly on unknown brands or a missing map instead of silently producing broken assets", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "devfeed-profile-icons-"));
  directories.push(directory);
  const component = path.join(directory, "map.ts");
  const assets = path.join(directory, "assets");
  await writeFile(component, "const icons = {};");
  await expect(generateProfileIcons(component, assets)).rejects.toThrow(
    "Profile icon map is missing",
  );
  await writeFile(
    component,
    'const icons = { brand: "/profile-icons/unknownbrand.000000000000.svg" };',
  );
  await expect(generateProfileIcons(component, assets)).rejects.toThrow(
    "Unknown Simple Icons slug",
  );
});

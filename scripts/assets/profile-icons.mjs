// Keep checked-in brand assets aligned with the locked Simple Icons dependency.
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import * as simpleIcons from "simple-icons";

export async function generateProfileIcons(
  component = "apps/web/src/components/profile-link-icon.tsx",
  directory = "apps/web/public/profile-icons",
) {
  const bySlug = new Map(Object.values(simpleIcons).map((icon) => [icon.slug, icon]));
  const source = await readFile(component, "utf8");
  const paths = [...new Set(source.match(/\/profile-icons\/[a-z0-9]+\.[a-f0-9]{12}\.svg/g))];
  if (!paths.length) throw new Error("Profile icon map is missing");
  await mkdir(directory, { recursive: true });
  let updated = source;
  for (const pathname of paths) {
    const slug = pathname.split("/").at(-1).split(".")[0];
    const icon = bySlug.get(slug);
    if (!icon) throw new Error(`Unknown Simple Icons slug: ${slug}`);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="${icon.path}"/></svg>\n`;
    const hash = createHash("sha256").update(svg).digest("hex").slice(0, 12);
    const next = `/profile-icons/${slug}.${hash}.svg`;
    await writeFile(path.join(directory, path.basename(next)), svg);
    updated = updated.replaceAll(pathname, next);
  }
  await writeFile(component, updated);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await generateProfileIcons();
}

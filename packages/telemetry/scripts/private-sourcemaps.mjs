import { mkdir, readdir, rename } from "node:fs/promises";
import path from "node:path";
const next = process.argv[2];
if (!next) throw new Error("Expected Next build directory");
const source = path.join(next, "static");
const destination = path.join(next, "faro-sourcemaps");
await mkdir(destination, { recursive: true });
async function move(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) await move(filename);
    else if (entry.name.endsWith(".map")) {
      const target = path.join(destination, path.relative(source, filename));
      await mkdir(path.dirname(target), { recursive: true });
      await rename(filename, target);
    }
  }
}
await move(source);

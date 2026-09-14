import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

for (const browser of ["chrome", "edge"]) {
  test(`${browser} store packaging omits the development key without changing the unpacked build`, async () => {
    const root = await mkdtemp(path.join(tmpdir(), "devfeed-package-test-"));
    try {
      await cp(new URL("../package.py", import.meta.url), path.join(root, "package.py"));
      const source = path.join(root, `dist/${browser}`);
      await mkdir(source, { recursive: true });
      const metadata = { manifest_version: 3, name: "Test", version: "0.1.0" };
      const update_url = "https://clients2.google.com/service/update2/crx";
      const manifest = { ...metadata, key: "dev-key", update_url };
      await writeFile(path.join(source, "manifest.json"), JSON.stringify(manifest));
      for (const name of ["newtab.html", "newtab.js", "newtab.css", "icon.png"])
        await writeFile(path.join(source, name), name);
      execFileSync("python3", [path.join(root, "package.py"), browser]);
      const zipped = JSON.parse(
        execFileSync("python3", [
          "-c",
          "import sys, zipfile; print(zipfile.ZipFile(sys.argv[1]).read('manifest.json').decode())",
          path.join(root, `dist/devfeed-new-tab${browser === "edge" ? "-edge" : ""}-0.1.0.zip`),
        ]).toString(),
      );
      assert.deepEqual(zipped, browser === "edge" ? metadata : { ...metadata, update_url });
      assert.deepEqual(JSON.parse(await readFile(path.join(source, "manifest.json"))), manifest);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

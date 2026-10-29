import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

for (const browser of ["chrome", "edge"]) {
  test(`${browser} store packaging preserves runtime assets and omits development files`, async () => {
    const root = await mkdtemp(path.join(tmpdir(), "devfeed-package-test-"));
    try {
      await cp(new URL("../package.py", import.meta.url), path.join(root, "package.py"));
      const source = path.join(root, `dist/${browser}`);
      await mkdir(source, { recursive: true });
      const metadata = { manifest_version: 3, name: "Test", version: "0.1.0" };
      const update_url = "https://clients2.google.com/service/update2/crx";
      const manifest = { ...metadata, key: "dev-key", update_url };
      await writeFile(path.join(source, "manifest.json"), JSON.stringify(manifest));
      const assets = new Map(
        ["newtab.html", "newtab.js", "newtab.css", "icon.png"].map((name) => [
          name,
          Buffer.from(name),
        ]),
      );
      for (const [name, original] of [
        ["favicon.png", "../../../packages/theme/assets/devfeed-icon-32.png"],
        [
          "profile-icons/github.e327163d1e9b.svg",
          "../../web/public/profile-icons/github.e327163d1e9b.svg",
        ],
        [
          "google-preferred-source/light.webp",
          "../../web/public/google-preferred-source/light.webp",
        ],
        ["google-preferred-source/dark.webp", "../../web/public/google-preferred-source/dark.webp"],
      ])
        assets.set(name, await readFile(new URL(original, import.meta.url)));
      assets.set(
        "tool-icons/nested/tool.svg",
        Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>'),
      );
      assets.set("tool-icons/tool.png", assets.get("favicon.png"));
      for (const [name, contents] of assets) {
        await mkdir(path.dirname(path.join(source, name)), { recursive: true });
        await writeFile(path.join(source, name), contents);
      }
      for (const name of [
        "newtab.js.map",
        "reader-debug.png",
        "tool-icons/tool.svg.map",
        "profile-icons/README.md",
        "google-preferred-source/README.md",
        "google-preferred-source/light.png",
        "google-preferred-source/dark.png",
      ])
        await writeFile(path.join(source, name), "Not a runtime asset");
      execFileSync("python3", [path.join(root, "package.py"), browser]);
      const contents = JSON.parse(
        execFileSync("python3", [
          "-c",
          "import base64, json, sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); print(json.dumps({name: base64.b64encode(z.read(name)).decode() for name in z.namelist()}))",
          path.join(root, `dist/devfeed-${browser}-extension-0.1.0.zip`),
        ]).toString(),
      );
      assert.deepEqual(Object.keys(contents).sort(), ["manifest.json", ...assets.keys()].sort());
      const zipped = JSON.parse(Buffer.from(contents["manifest.json"], "base64").toString());
      assert.deepEqual(zipped, browser === "edge" ? metadata : { ...metadata, update_url });
      for (const [name, expected] of assets)
        assert.deepEqual(
          Buffer.from(contents[name], "base64"),
          expected,
          `${name} survives ZIP compression unchanged`,
        );
      assert.deepEqual(JSON.parse(await readFile(path.join(source, "manifest.json"))), manifest);
      await rm(path.join(source, "google-preferred-source/dark.webp"));
      assert.throws(
        () => execFileSync("python3", [path.join(root, "package.py"), browser], { stdio: "pipe" }),
        /dark\.webp/,
        "A missing required badge must fail packaging instead of producing a broken release",
      );
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}

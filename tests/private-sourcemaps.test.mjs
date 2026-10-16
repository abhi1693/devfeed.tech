import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execute = promisify(execFile);
const script = fileURLToPath(
  new URL("../packages/telemetry/scripts/private-sourcemaps.mjs", import.meta.url),
);

test("moves maps in an explicitly selected build directory without following directory links", async () => {
  const temporary = await mkdtemp(path.join(tmpdir(), "devfeed-private-sourcemaps-"));
  try {
    const build = path.join(temporary, "build outside the checkout");
    const source = path.join(build, "static", "chunks");
    const external = path.join(temporary, "other directory");
    await mkdir(source, { recursive: true });
    await mkdir(external);
    await writeFile(path.join(source, "page.js.map"), "private map");
    await writeFile(path.join(source, "page.js"), "public JavaScript");
    await writeFile(path.join(external, "other.js.map"), "unrelated map");
    await symlink(external, path.join(build, "static", "linked-directory"), "dir");

    await execute(process.execPath, [script, build]);

    assert.equal(
      await readFile(path.join(build, "faro-sourcemaps", "chunks", "page.js.map"), "utf8"),
      "private map",
    );
    await assert.rejects(readFile(path.join(source, "page.js.map")), { code: "ENOENT" });
    assert.equal(await readFile(path.join(source, "page.js"), "utf8"), "public JavaScript");
    assert.equal(await readFile(path.join(external, "other.js.map"), "utf8"), "unrelated map");
    await assert.rejects(readFile(path.join(build, "faro-sourcemaps", "other.js.map")), {
      code: "ENOENT",
    });
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
});

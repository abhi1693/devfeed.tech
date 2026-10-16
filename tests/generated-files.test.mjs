import assert from "node:assert/strict";
import { constants } from "node:fs";
import fs, {
  mkdir,
  mkdtemp,
  open,
  readFile,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { readRegularFile, writeGeneratedFile } from "../scripts/lib/generated-files.mjs";

const marker = "// Generated for this test. Do not edit.";
const ownership = { marker, label: "test operation definition" };

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-generated-file-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return {
    directory,
    target: join(directory, "operations.ts"),
    moved: join(directory, "original.ts"),
    outside: join(directory, "handwritten.ts"),
  };
}

async function afterOpenedFileCheck(t, target, replace) {
  const handle = await open(target, "r");
  const prototype = Object.getPrototypeOf(handle);
  await handle.close();
  const original = prototype.stat;
  let replaced = false;
  t.mock.method(prototype, "stat", async function (...args) {
    const result = await original.apply(this, args);
    if (!replaced) {
      replaced = true;
      await replace();
    }
    return result;
  });
}

test("reading a validated file keeps its identity when its pathname becomes a symlink", async (t) => {
  const { target, moved, outside } = await fixture(t);
  await writeFile(target, "export const GET = safe;\n");
  await writeFile(outside, "export const POST = unsafe;\n");
  await afterOpenedFileCheck(t, target, async () => {
    await rename(target, moved);
    await symlink(outside, target);
  });
  assert.equal(await readRegularFile(target, "test route"), "export const GET = safe;\n");
  assert.equal(await readFile(outside, "utf8"), "export const POST = unsafe;\n");
});

for (const replacement of ["symlink", "handwritten file"]) {
  test(`generated writes cannot be redirected to a replacement ${replacement}`, async (t) => {
    const { target, moved, outside } = await fixture(t);
    await writeFile(target, `${marker}\nold generated content with a long suffix\n`);
    await writeFile(outside, "handwritten outside\n");
    await afterOpenedFileCheck(t, target, async () => {
      await rename(target, moved);
      if (replacement === "symlink") await symlink(outside, target);
      else await writeFile(target, "handwritten replacement\n");
    });
    const generated = `${marker}\nnew\n`;
    await writeGeneratedFile(target, generated, ownership);
    assert.equal(await readFile(moved, "utf8"), generated);
    assert.equal(await readFile(outside, "utf8"), "handwritten outside\n");
    assert.equal(
      await readFile(target, "utf8"),
      replacement === "symlink" ? "handwritten outside\n" : "handwritten replacement\n",
    );
  });
}

test("exclusive creation refuses a handwritten file arriving after an initial missing-file result", async (t) => {
  const { target } = await fixture(t);
  const original = fs.open;
  let raced = false;
  t.mock.method(fs, "open", async (...args) => {
    if (!raced && args[0] === target && args[1] & constants.O_CREAT) {
      raced = true;
      await writeFile(target, "handwritten concurrent creation\n");
    }
    return original(...args);
  });
  syncBuiltinESMExports();
  t.after(() => {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  });
  await assert.rejects(writeGeneratedFile(target, `${marker}\nnew\n`, ownership), /handwritten/);
  assert.equal(raced, true);
  assert.equal(await readFile(target, "utf8"), "handwritten concurrent creation\n");
});

test("generated files reject symlinks and directories without modifying their targets", async (t) => {
  const { target, outside, directory } = await fixture(t);
  await writeFile(outside, `${marker}\nleave this alone\n`);
  await symlink(outside, target);
  await assert.rejects(readRegularFile(target, "test route"), /symlinked/);
  await assert.rejects(writeGeneratedFile(target, `${marker}\nnew\n`, ownership), /symlinked/);
  const folder = join(directory, "folder");
  await mkdir(folder);
  await assert.rejects(readRegularFile(folder, "test route"), /non-file/);
  await assert.rejects(writeGeneratedFile(folder, `${marker}\nnew\n`, ownership), /non-file/);
  assert.equal(await readFile(outside, "utf8"), `${marker}\nleave this alone\n`);
});

test("generated files can be created and shortened without leaving old bytes", async (t) => {
  const { target } = await fixture(t);
  await writeGeneratedFile(target, `${marker}\n${"long content".repeat(1000)}\n`, ownership);
  const next = `${marker}\nconst value = "🎉";\n`;
  await writeGeneratedFile(target, next, ownership);
  assert.equal(await readRegularFile(target, "test route"), next);
});

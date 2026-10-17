import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { nextBuildCachePlan } from "../.github/actions/next-build-cache/key.mjs";

const helper = fileURLToPath(
  new URL("../.github/actions/next-build-cache/key.mjs", import.meta.url),
);
const source = "a".repeat(40);
const environment = { platform: "linux", arch: "x64", node: "24.21.0" };

async function put(root, filename, content) {
  const destination = join(root, filename);
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, content);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "devfeed-next-cache-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await put(root, "package.json", '{"name":"reader-workspace"}\n');
  await put(root, "package-lock.json", '{"packages":{"next":{"version":"16.4.1"}}}\n');
  for (const app of ["web", "admin", "partner"]) {
    await put(root, `apps/${app}/package.json`, `{"name":"@devfeed/${app}"}\n`);
    await put(root, `apps/${app}/tsconfig.json`, '{"compilerOptions":{"strict":true}}\n');
    await put(root, `apps/${app}/next.config.ts`, 'export default { output: "standalone" };\n');
  }
  return {
    root,
    plan: (overrides = {}) =>
      nextBuildCachePlan({
        root,
        app: "web",
        profile: "production",
        source,
        environment,
        ...overrides,
      }),
  };
}

test("source revisions can reuse compatible compiler work without sharing an immutable entry", async (t) => {
  const { root, plan } = await fixture(t);
  const before = await plan();
  await put(
    root,
    "apps/web/src/app/page.tsx",
    "export default function Page() { return 'Changed'; }",
  );
  const after = await plan({ source: "b".repeat(40) });
  assert.notEqual(before.key, after.key);
  assert.equal(before.restorePrefix, after.restorePrefix);
  assert.ok(before.key.startsWith(before.restorePrefix));
  assert.ok(after.key.startsWith(after.restorePrefix));
  assert.ok(after.key.length <= 512);
});

test("application, build profile, actual Node, OS and architecture never share a fallback", async (t) => {
  const { plan } = await fixture(t);
  const baseline = await plan();
  for (const overrides of [
    { app: "admin" },
    { app: "partner" },
    { profile: "dast" },
    { environment: { ...environment, node: "26.0.0" } },
    { environment: { ...environment, platform: "darwin" } },
    { environment: { ...environment, arch: "arm64" } },
  ]) {
    const changed = await plan(overrides);
    assert.notEqual(changed.restorePrefix, baseline.restorePrefix);
    assert.equal(changed.key.startsWith(baseline.restorePrefix), false);
  }
});

for (const [name, filename, content] of [
  ["Next dependency upgrade", "package-lock.json", '{"next":"16.4.2"}\n'],
  ["workspace build settings", "package.json", '{"name":"reader-workspace","version":"2"}\n'],
  ["root TypeScript configuration", "tsconfig.base.json", '{"compilerOptions":{"strict":true}}'],
  ["application version", "apps/web/package.json", '{"name":"@devfeed/web","version":"2"}\n'],
  [
    "Next build configuration",
    "apps/web/next.config.ts",
    "export default { reactCompiler: true };",
  ],
  ["TypeScript configuration", "apps/web/tsconfig.json", '{"compilerOptions":{"strict":false}}'],
  ["new CSS build configuration", "apps/web/postcss.config.mjs", "export default { plugins: {} };"],
]) {
  test(`${name} cannot restore the previous compatibility cache`, async (t) => {
    const { root, plan } = await fixture(t);
    const before = await plan();
    await put(root, filename, content);
    const after = await plan();
    assert.notEqual(after.restorePrefix, before.restorePrefix);
    assert.equal(after.key.startsWith(before.restorePrefix), false);
  });
}

test("the cache selection includes compiler files and excludes runtime data and production output", async (t) => {
  const { root, plan } = await fixture(t);
  const compiled = [
    "apps/web/.next/cache/turbopack/cache.bin",
    "apps/web/.next/cache/webpack/client/0.pack",
    "apps/web/.next/cache/swc/plugin.bin",
    "apps/web/.next/cache/.tsbuildinfo",
  ];
  const excluded = [
    "apps/web/.next/cache/fetch-cache/private-response",
    "apps/web/.next/cache/images/avatar",
    "apps/web/.next/server/app/page.js",
    "apps/web/.next/standalone/server.js",
    "apps/web/.next/static/chunk.js",
    "apps/web/.next/faro-sourcemaps/chunk.js.map",
    "apps/web/.next/BUILD_ID",
    "reports/browser/profile",
    ".env",
  ];
  for (const filename of [...compiled, ...excluded]) await put(root, filename, "Preserve content");
  const selection = await plan();
  const selected = [];
  for (const filename of selection.paths) {
    if (filename.endsWith(".tsbuildinfo")) selected.push(filename);
    else {
      for (const child of await readdir(join(root, filename), {
        recursive: true,
        withFileTypes: true,
      })) {
        if (child.isFile()) selected.push(relative(root, join(child.parentPath, child.name)));
      }
    }
  }
  assert.deepEqual(selected.sort(), compiled.sort());
  for (const filename of excluded)
    assert.equal(await readFile(join(root, filename), "utf8"), "Preserve content");
});

test("the action emits GitHub outputs using the executing Node without creating a fake cache", async (t) => {
  const { root } = await fixture(t);
  const output = join(root, "outputs");
  const result = spawnSync(process.execPath, [helper], {
    encoding: "utf8",
    env: {
      ...process.env,
      GITHUB_WORKSPACE: root,
      GITHUB_OUTPUT: output,
      GITHUB_SHA: source,
      NEXT_CACHE_APP: "web",
      NEXT_CACHE_PROFILE: "production",
    },
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const outputs = await readFile(output, "utf8");
  assert.ok(outputs.includes(`-node${process.versions.node}-web-production-`));
  assert.ok(outputs.includes("paths<<NEXT_COMPILER_CACHE_PATHS\napps/web/.next/cache/turbopack\n"));
  await assert.rejects(readdir(join(root, "apps/web/.next")), { code: "ENOENT" });
});

test("invalid application/profile/source inputs fail before producing cache paths", async (t) => {
  const { plan } = await fixture(t);
  for (const overrides of [
    { app: "../web" },
    { app: "api" },
    { profile: "" },
    { profile: "production\npath=.." },
    { profile: "private/profile" },
    { source: "master" },
    { source: `${source}\nextra=output` },
    { environment: { ...environment, arch: "x64\npath=.." } },
  ])
    await assert.rejects(plan(overrides));
});

test("missing or symlinked compatibility inputs fail instead of selecting a broad cache", async (t) => {
  const { root, plan } = await fixture(t);
  await rm(join(root, "package-lock.json"));
  await assert.rejects(plan(), { code: "ENOENT" });
  await put(root, "outside-lock", "Do not use a different file");
  await symlink(join(root, "outside-lock"), join(root, "package-lock.json"));
  await assert.rejects(plan(), /regular file/);
});

test("a non-regular compatibility input is rejected without blocking on a FIFO", async (t) => {
  const { root } = await fixture(t);
  const lock = join(root, "package-lock.json");
  await rm(lock);
  const fifo = spawnSync("mkfifo", [lock], { encoding: "utf8" });
  assert.equal(fifo.status, 0, fifo.stderr);
  const result = spawnSync(process.execPath, [helper], {
    encoding: "utf8",
    timeout: 5000,
    env: {
      ...process.env,
      GITHUB_WORKSPACE: root,
      GITHUB_OUTPUT: join(root, "outputs"),
      GITHUB_SHA: source,
      NEXT_CACHE_APP: "web",
      NEXT_CACHE_PROFILE: "production",
    },
  });
  assert.equal(result.error, undefined, result.error?.message);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /regular file/);
});

test("the composite consumes only the computed paths and pins its cache action", async () => {
  const action = await readFile(
    new URL("../.github/actions/next-build-cache/action.yml", import.meta.url),
    "utf8",
  );
  assert.match(action, /uses: actions\/cache@[a-f0-9]{40} /);
  assert.ok(action.includes("path: ${{ steps.plan.outputs.paths }}"));
  assert.ok(action.includes("key: ${{ steps.plan.outputs.key }}"));
  assert.ok(action.includes("restore-keys: ${{ steps.plan.outputs.restore-prefix }}"));
  assert.doesNotMatch(action, /continue-on-error|skip-build|mkdir|node_modules|\.next\/standalone/);
});

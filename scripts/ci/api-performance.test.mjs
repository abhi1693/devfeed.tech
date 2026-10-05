import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { affectsApi, changedPaths } from "./api-performance-changes.mjs";

const selector = fileURLToPath(new URL("./api-performance-changes.mjs", import.meta.url));
const selectorModule = new URL("./api-performance-changes.mjs", import.meta.url).href;

function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), "devfeed-api-selection-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const git = (...args) =>
    execFileSync("/usr/bin/git", args, {
      cwd: directory,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 30_000,
    }).trim();
  git("init", "--quiet");
  const write = (path, content) => {
    const target = join(directory, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, content);
  };
  const commit = () => {
    git("add", "--all");
    git(
      "-c",
      "user.name=API selector test",
      "-c",
      "user.email=api-selector@example.invalid",
      "-c",
      "commit.gpgsign=false",
      "commit",
      "--quiet",
      "-m",
      "Fixture",
    );
    return git("rev-parse", "HEAD");
  };
  const paths = (base, head) =>
    JSON.parse(
      execFileSync(
        process.execPath,
        [
          "--input-type=module",
          "-e",
          `import { changedPaths } from ${JSON.stringify(selectorModule)};
          process.stdout.write(JSON.stringify(changedPaths(${JSON.stringify(base)}, ${JSON.stringify(head)})));`,
        ],
        {
          cwd: directory,
          env: { ...process.env, PATH: "" },
          encoding: "utf8",
          timeout: 30_000,
        },
      ),
    );
  const cli = (event, overrides = {}) => {
    const eventPath = join(directory, "event.json");
    const outputPath = join(directory, "output.txt");
    writeFileSync(eventPath, typeof event === "string" ? event : JSON.stringify(event));
    rmSync(outputPath, { force: true });
    const result = spawnSync(process.execPath, [selector], {
      cwd: directory,
      env: {
        ...process.env,
        PATH: "",
        GITHUB_EVENT_PATH: eventPath,
        GITHUB_OUTPUT: outputPath,
        ...overrides,
      },
      encoding: "utf8",
      timeout: 30_000,
    });
    return { ...result, output: existsSync(outputPath) ? readFileSync(outputPath, "utf8") : "" };
  };
  return { directory, write, commit, paths, cli };
}

test("the three frontend-only Sonar PRs do not benchmark unchanged Python inputs", () => {
  for (const paths of [
    ["packages/braces/lib/utils.js"],
    ["scripts/testing/dev-card.mjs", "scripts/testing/profile-editor.mjs"],
    ["apps/web/src/components/feed-filters.tsx", "apps/web/tests/feed-filters.test.tsx"],
  ])
    assert.equal(affectsApi(paths), false, paths.join(", "));
});

test("known frontend consumers and assets stay outside the public API workload", () => {
  for (const path of [
    "apps/web/public/favicon.ico",
    "apps/admin/src/components/overview.tsx",
    "apps/extensions/build.mjs",
    "apps/extensions/tests/feed.browser.mjs",
    "packages/ui/src/date-format.ts",
    "packages/theme/assets/devfeed-icon-32.png",
    "packages/telemetry/src/faro.ts",
    "packages/braces/package.json",
    "apps/web/public/fonts/reader.woff2",
    "apps/web/public/a\nicon.svg",
  ])
    assert.equal(affectsApi([path]), false, path);
  assert.equal(affectsApi([]), false);
});

test("runtime, dependencies, migrations, harness, workflows and unknown inputs run", () => {
  for (const path of [
    "apps/api/src/feed.py",
    "apps/admin-api/src/users.py",
    "packages/core/src/models.py",
    "packages/http/src/cache.py",
    "apps/new-service/src/main.py",
    "migrations/versions/catalog.sql",
    "alembic.ini",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    "apps/api/pyproject.toml",
    "scripts/load-test.py",
    "loadtests/locustfile.py",
    "loadtests/compare.py",
    "scripts/ci/python-bootstrap.sh",
    "scripts/ci/api-performance-changes.mjs",
    ".github/workflows/performance.yml",
    ".github/workflows/ci.yml",
    "package-lock.json",
    "apps/web/Dockerfile",
    "README.md",
    "unknown/input.json",
    "apps/web/src/runtime.wasm",
    "",
  ]) {
    assert.equal(affectsApi([path]), true, path);
    assert.equal(affectsApi(["apps/web/src/app/page.tsx", path]), true, path);
  }
});

test("Python and database inputs override recognized frontend directories", () => {
  for (const directory of [
    "apps/web",
    "apps/admin",
    "apps/extensions",
    "packages/ui",
    "packages/braces",
    "scripts/testing",
  ])
    for (const file of ["server.py", "server.pyi", "schema.sql", "pyproject.toml", "uv.lock"])
      assert.equal(affectsApi([`${directory}/${file}`]), true, `${directory}/${file}`);
});

test("unsafe, missing and malformed refs fail before Git receives them", () => {
  for (const ref of [
    undefined,
    null,
    123,
    "",
    "master",
    "HEAD",
    "--output=/tmp/report",
    "$(id)",
    "A".repeat(40),
    "a".repeat(39),
    "a".repeat(41),
  ]) {
    assert.throws(() => changedPaths(ref, "a".repeat(40)), /valid base and head commits/);
    assert.throws(() => changedPaths("a".repeat(40), ref), /valid base and head commits/);
  }
});

test("exact refs and system Git preserve deleted and renamed paths", (t) => {
  const f = fixture(t);
  f.write("packages/core/src/model.py", "value = 1\n");
  f.write("apps/web/public/a\nicon.svg", "<svg/>\n");
  const base = f.commit();
  assert.deepEqual(f.paths(base, base), []);
  mkdirSync(join(f.directory, "apps/web/src"), { recursive: true });
  renameSync(
    join(f.directory, "packages/core/src/model.py"),
    join(f.directory, "apps/web/src/vendor.js"),
  );
  rmSync(join(f.directory, "apps/web/public/a\nicon.svg"));
  const head = f.commit();
  const paths = f.paths(base, head);
  assert.deepEqual(
    paths.sort((a, b) => a.localeCompare(b, "en")),
    ["apps/web/public/a\nicon.svg", "apps/web/src/vendor.js", "packages/core/src/model.py"],
  );
  assert.equal(affectsApi(paths), true);
});

test("the CLI selects exact event commits even when checkout HEAD differs", (t) => {
  const f = fixture(t);
  f.write("apps/web/src/page.tsx", "export const page = 1;\n");
  const base = f.commit();
  f.write("apps/web/src/page.tsx", "export const page = 2;\n");
  const frontendHead = f.commit();
  f.write("apps/web/src/server.py", "value = 1\n");
  const runtimeHead = f.commit();
  for (const [head, expected] of [
    [frontendHead, false],
    [runtimeHead, true],
    [base, false],
  ]) {
    const result = f.cli({ pull_request: { base: { sha: base }, head: { sha: head } } });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.output, `needed=${expected}\n`);
  }
});

test("CLI event, output and Git failures never emit a false skip", (t) => {
  const f = fixture(t);
  f.write("apps/web/src/page.tsx", "export const page = 1;\n");
  const head = f.commit();
  const event = { pull_request: { base: { sha: head }, head: { sha: head } } };
  for (const [payload, overrides] of [
    [event, { GITHUB_EVENT_PATH: "" }],
    [event, { GITHUB_OUTPUT: "" }],
    ["not json", {}],
    [{}, {}],
    [{ merge_group: { base_sha: head, head_sha: head } }, {}],
    [{ pull_request: { base: {}, head: { sha: head } } }, {}],
    [{ pull_request: { base: { sha: head }, head: {} } }, {}],
    [{ pull_request: { base: { sha: head }, head: { sha: "f".repeat(40) } } }, {}],
  ]) {
    const result = f.cli(payload, overrides);
    assert.notEqual(result.status, 0);
    assert.equal(result.output, "", result.stderr);
  }
});

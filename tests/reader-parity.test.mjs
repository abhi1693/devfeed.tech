import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  assertPartnerCallerProofs,
  partnerRuntimePlan,
  partnerRuntimes,
} from "../apps/web/tests/browser/partner-runtimes.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const command = (...args) => ({ command: args, platform: null });
const history = (platform) => ({
  command: ["node", "tests/benchmarks/reader-history.mjs"],
  platform,
});
const web = [
  command("node", "--test", "apps/web/tests/browser/accessibility.browser.mjs"),
  command("npm", "run", "web:build"),
  command("node", "apps/web/tests/browser/document-streaming.mjs"),
  history(null),
  command("node", "apps/web/tests/browser/home-entry.mjs"),
  command("node", "apps/web/tests/browser/structured-data.mjs"),
  command("node", "--test", "apps/web/tests/browser/feed.mjs"),
  command("node", "--test", "apps/web/tests/browser/login-recovery.mjs"),
  command("node", "apps/web/tests/browser/telemetry.mjs"),
  command("node", "apps/web/tests/browser/x-attribution.mjs"),
  command("node", "apps/web/tests/browser/profile.mjs"),
  command("node", "apps/web/tests/browser/reader-gateway.mjs"),
];
const chrome = [command("npm", "run", "extension:test:browser"), history("chrome")];
const edge = [command("npm", "run", "extension:edge:test:browser"), history("edge")];
const partnerBuilds = [
  command("npm", "run", "web:build"),
  command("npm", "run", "extension:build"),
  command("npm", "run", "extension:edge:build"),
];
const partnerPrograms = [
  command("node", "apps/web/tests/browser/partner-tracking.mjs"),
  command("node", "apps/web/tests/browser/partner-placements.mjs"),
];
const legacyPartner = [...partnerBuilds, ...partnerPrograms];
const selectedPlacements = (runtime) =>
  command("node", "apps/web/tests/browser/partner-placements.mjs", `--runtime=${runtime}`);
const suites = {
  web,
  chrome,
  edge,
  "partner-web": [...partnerBuilds, partnerPrograms[0], selectedPlacements("web")],
  "partner-chrome": [partnerBuilds[0], partnerBuilds[1], selectedPlacements("chrome")],
  "partner-edge": [partnerBuilds[0], partnerBuilds[2], selectedPlacements("edge")],
};
const legacySuites = { web, chrome, edge, partner: legacyPartner };
const installation = (suite) =>
  command(
    "npx",
    "--no-install",
    "--ignore-scripts",
    "playwright",
    "install",
    "--with-deps",
    ...(["web", "chrome", "partner-chrome"].includes(suite)
      ? ["chromium"]
      : ["chromium", "msedge"]),
  );

async function execute(t, args = [], failures = []) {
  const directory = await mkdtemp(path.join(tmpdir(), "devfeed-reader-dispatch-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const bin = path.join(directory, "bin");
  const log = path.join(directory, "commands.jsonl");
  const summary = path.join(directory, "summary.md");
  await mkdir(bin);
  for (const program of ["node", "npm", "npx"]) {
    await writeFile(
      path.join(bin, program),
      `#!${process.execPath}
import { appendFileSync, closeSync, openSync, unlinkSync } from "node:fs";
import path from "node:path";
const command = [path.basename(process.argv[1]), ...process.argv.slice(2)];
const entry = {
  command,
  platform: command.includes("tests/benchmarks/reader-history.mjs")
    ? process.env.DEVFEED_HISTORY_PLATFORM ?? null : null,
};
// Concurrent programs in one checkout must fail, even if their start order matches.
closeSync(openSync(process.env.READER_FAKE_LOCK, "wx"));
let code = 0;
try {
  appendFileSync(process.env.READER_FAKE_LOG, JSON.stringify(entry) + "\\n");
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
  if (JSON.parse(process.env.READER_FAKE_FAILURES).some(value => JSON.stringify(value) === JSON.stringify(entry))) code = 7;
} finally {
  unlinkSync(process.env.READER_FAKE_LOCK);
}
process.exit(code);
`,
      { mode: 0o755 },
    );
  }
  const env = {
    ...process.env,
    PATH: `${bin}${path.delimiter}${process.env.PATH}`,
    READER_FAKE_LOG: log,
    READER_FAKE_LOCK: path.join(directory, "command.lock"),
    READER_FAKE_FAILURES: JSON.stringify(failures),
    GITHUB_STEP_SUMMARY: summary,
  };
  delete env.DEVFEED_HISTORY_PLATFORM;
  const result = spawnSync("bash", ["scripts/ci/reader-parity.sh", ...args], {
    cwd: root,
    env,
    encoding: "utf8",
    timeout: 10_000,
  });
  assert.equal(result.error, undefined, result.error?.message);
  const contents = await readFile(log, "utf8").catch((error) => {
    if (error.code === "ENOENT") return "";
    throw error;
  });
  return {
    ...result,
    commands: contents.trim() ? contents.trim().split("\n").map(JSON.parse) : [],
    summary: await readFile(summary, "utf8").catch((error) => {
      if (error.code === "ENOENT") return "";
      throw error;
    }),
  };
}

test("isolated suites execute only their complete commands and required browser setup", async (t) => {
  for (const [suite, expected] of Object.entries({ ...suites, partner: legacyPartner })) {
    const result = await execute(t, [suite]);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.commands, [installation(suite), ...expected], suite);
    assert.match(result.stdout, new RegExp(`Reader parity: ${suite} \\(exit 0\\)`));
    assert.match(result.summary, /UTC start.*Duration \(s\).*Exit/);
    assert.match(result.summary, /\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ/);
  }
});

test("all 18 browser commands belong to exactly one suite, with explicit partner prerequisites", () => {
  const originals = [...web, ...chrome, ...edge, ...partnerPrograms];
  assert.equal(originals.length, 18);
  assert.equal(new Set(originals.map((value) => JSON.stringify(value))).size, 18);
  assert.deepEqual(partnerBuilds, [
    web[1],
    command("npm", "run", "extension:build"),
    command("npm", "run", "extension:edge:build"),
  ]);
});

test("the default local entry runs every suite sequentially without losing original checks", async (t) => {
  const expected = [installation("all"), ...web, ...chrome, ...edge, ...legacyPartner];
  for (const args of [[], ["all"]]) {
    const result = await execute(t, args);
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(result.commands, expected);
    assert.equal(result.commands.length, 22, "18 browser commands, three prerequisites, install");
  }
});

test("invalid selectors stop before installing browsers or executing a check", async (t) => {
  for (const args of [["unknown"], [""], ["web", "edge"], ["--help"]]) {
    const result = await execute(t, args);
    assert.equal(result.status, 2);
    assert.deepEqual(result.commands, []);
    assert.equal(result.summary, "");
    assert.match(result.stderr, /Unknown reader suite|Usage:/);
  }
});

test("each failed check keeps its suite red and still attempts the remaining independent checks", async (t) => {
  for (const [suite, failure] of [
    ["web", web[0]],
    ["chrome", chrome[0]],
    ["edge", edge[0]],
    ["partner", partnerPrograms[0]],
    ["partner-web", selectedPlacements("web")],
    ["partner-chrome", selectedPlacements("chrome")],
    ["partner-edge", selectedPlacements("edge")],
  ]) {
    const result = await execute(t, [suite], [failure]);
    assert.equal(result.status, 1, suite);
    assert.deepEqual(
      result.commands,
      [installation(suite), ...(suite === "partner" ? legacyPartner : suites[suite])],
      suite,
    );
    assert.match(result.stdout, /exit=7 duration=\d+s/);
    assert.match(result.summary, /\| 7 \|/);
  }
});

test("failure in one default suite never suppresses another runtime or clears the failure", async (t) => {
  const result = await execute(t, [], [web[0], chrome[0], edge[0], partnerPrograms[0]]);
  assert.equal(result.status, 1);
  assert.deepEqual(result.commands, [installation("all"), ...Object.values(legacySuites).flat()]);
});

test("a failed web build skips only its dependent checks and preserves the failure", async (t) => {
  const isolated = await execute(t, ["web"], [web[1]]);
  assert.equal(isolated.status, 1);
  assert.deepEqual(isolated.commands, [installation("web"), ...web.slice(0, 2)]);

  const combined = await execute(t, [], [web[1]]);
  assert.equal(combined.status, 1);
  assert.deepEqual(combined.commands, [
    installation("all"),
    ...web.slice(0, 2),
    ...chrome,
    ...edge,
    ...partnerBuilds,
  ]);
});

test("every partner prerequisite is attempted but any failed build prevents stale-output journeys", async (t) => {
  for (const failure of partnerBuilds) {
    const result = await execute(t, ["partner"], [failure]);
    assert.equal(result.status, 1);
    assert.deepEqual(result.commands, [installation("partner"), ...partnerBuilds]);
  }
});

test("browser installation failure is fatal before any build or browser check", async (t) => {
  const result = await execute(t, ["edge"], [installation("edge")]);
  assert.equal(result.status, 7);
  assert.deepEqual(result.commands, [installation("edge")]);
  assert.match(result.summary, /Reader parity: edge \(exit 7\)/);
});

test("each selected partner runtime keeps fresh gateway and only required extension builds", async (t) => {
  for (const runtime of ["web", "chrome", "edge"]) {
    const suite = `partner-${runtime}`;
    const expected = suites[suite];
    for (const failure of expected.filter(({ command }) => command[0] === "npm")) {
      const result = await execute(t, [suite], [failure]);
      assert.equal(result.status, 1);
      assert.deepEqual(result.commands, [
        installation(suite),
        ...expected.filter(({ command }) => command[0] === "npm"),
      ]);
    }
  }
});

test("six isolated suites retain every original check and all partner runtimes exactly once", () => {
  assert.deepEqual(Object.keys(suites), [
    "web",
    "chrome",
    "edge",
    "partner-web",
    "partner-chrome",
    "partner-edge",
  ]);
  const isolated = Object.values(suites).flat();
  assert.equal(
    isolated.filter((entry) => JSON.stringify(entry) === JSON.stringify(partnerPrograms[0])).length,
    1,
  );
  const placements = isolated.filter(({ command }) =>
    command.includes("apps/web/tests/browser/partner-placements.mjs"),
  );
  assert.deepEqual(placements, ["web", "chrome", "edge"].map(selectedPlacements));
  assert.deepEqual(Object.values(suites).slice(0, 3).flat(), [...web, ...chrome, ...edge]);
});

test("partner runtime defaults and focused modes retain all three actual runtimes", () => {
  const expected = ["web", "chrome", "edge"];
  assert.deepEqual(partnerRuntimes(), expected);
  for (const mode of ["--search-context", "--link-rel", "--attribution", "--compact-feed"]) {
    assert.deepEqual(partnerRuntimes([mode]), expected);
    for (const runtime of expected) {
      assert.deepEqual(partnerRuntimes([mode, `--runtime=${runtime}`]), [runtime]);
      assert.deepEqual(partnerRuntimes([`--runtime=${runtime}`, mode]), [runtime]);
    }
  }
  const isolated = partnerRuntimes();
  isolated.pop();
  assert.deepEqual(partnerRuntimes(), expected);
});

test("partner runtime parser rejects invalid selectors before fixture startup", () => {
  for (const args of [
    ["--runtime="],
    ["--runtime=all"],
    ["--runtime=firefox"],
    ["--runtime=Chrome"],
    ["--runtime=web", "--runtime=edge"],
    ["--runtime=web", "--runtime=web"],
    ["--runtime", "web"],
    ["web"],
    ["--unknown"],
    ["--runtime=web\n"],
  ])
    assert.throws(() => partnerRuntimes(args), /Usage:/);
});

test("selected plans keep the original same-gateway three-caller check before their full matrix", () => {
  assert.deepEqual(
    partnerRuntimePlan(),
    ["web", "chrome", "edge"].map((platform) => ({
      platform,
      caller: platform,
      probe: false,
    })),
  );
  for (const platform of ["web", "chrome", "edge"]) {
    const plan = partnerRuntimePlan([`--runtime=${platform}`]);
    assert.deepEqual(plan, [
      ...["web", "chrome", "edge"].map((caller) => ({ platform, caller, probe: true })),
      { platform, caller: platform, probe: false },
    ]);
  }
});

test("same-gateway caller proof checks reject the cached-first-caller bug on the second browser", () => {
  const paths = ["/v1/partner-placements/query", "/v1/partner-tracking/events"];
  const first = "203.0.113.20";
  const cached = new Map(paths.map((pathname) => [pathname, new Set([first])]));
  assert.doesNotThrow(() => assertPartnerCallerProofs(cached, first));
  assert.throws(() => assertPartnerCallerProofs(cached, "203.0.113.30"), /same gateway/);
  for (const ip of [first, "203.0.113.30", "203.0.113.40"]) {
    const current = new Map(paths.map((pathname) => [pathname, new Set([ip])]));
    assert.doesNotThrow(() => assertPartnerCallerProofs(current, ip));
    current.delete(paths[1]);
    assert.throws(() => assertPartnerCallerProofs(current, ip), /same gateway/);
    current.set(paths[1], new Set([first, ip]));
    if (ip !== first) assert.throws(() => assertPartnerCallerProofs(current, ip), /same gateway/);
  }
});

test("runtime validation precedes resources and the original same-process three-caller assertion remains", async () => {
  const source = await readFile(
    new URL("../apps/web/tests/browser/partner-placements.mjs", import.meta.url),
    "utf8",
  );
  const validation = source.indexOf(
    "const runtimePlan = partnerRuntimePlan(process.argv.slice(2))",
  );
  assert.ok(validation >= 0);
  for (const resource of [
    "const temp = await mkdtemp",
    "const fixture = createServer",
    "const app = spawn",
  ]) {
    assert.ok(source.indexOf(resource) > validation, resource);
  }
  assert.equal(source.match(/const app = spawn\(/g).length, 1);
  assert.equal(source.match(/observedCallerIps\.add\(ip\)/g).length, 1);
  assert.ok(
    source.includes(
      'observedCallerIps.size,\n    3,\n    "The gateway keeps all three anonymous reader callers distinct"',
    ),
  );
  assert.ok(source.indexOf("const app = spawn") < source.indexOf("of runtimePlan)"));
});

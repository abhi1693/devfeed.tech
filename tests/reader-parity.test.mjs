import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm, writeFile, mkdir, stat, chmod, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createContext, runInContext } from "node:vm";
import { createReaderArtifactDirectory } from "../scripts/testing/reader-artifacts.mjs";
import { settleStackedTouchScroll } from "../scripts/testing/stacked-reader.mjs";
import {
  assertPartnerCallerProofs,
  partnerRuntimePlan,
  partnerRuntimes,
} from "../apps/web/tests/browser/partner-runtimes.mjs";
import {
  runPlacementStage,
  waitForPlacementServer,
  withPlacementDeliveryHeld,
} from "../apps/web/tests/browser/partner-fixture.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));

function scrollFixture(y, saved) {
  let waited;
  let complete;
  const waiting = new Promise((resolve) => {
    waited = resolve;
  });
  const state = createContext({
    scrollX: 0,
    scrollY: y,
    history: { state: saved },
    frames: 0,
    requestAnimationFrame: (callback) => {
      state.frames++;
      queueMicrotask(callback);
    },
  });
  state.window = {
    scrollTo: (x, y) => {
      state.scrollX = x;
      state.scrollY = y;
    },
  };
  const evaluate = (fn) => runInContext(`(${fn.toString()})()`, state);
  return {
    state,
    waiting,
    page: {
      evaluate,
      waitForFunction: (predicate) => {
        waited();
        if (evaluate(predicate)) return Promise.resolve();
        return new Promise((resolve) => {
          complete = () => {
            assert.equal(evaluate(predicate), true);
            resolve();
          };
        });
      },
    },
    save: () => {
      state.history.state = { readerScroll: [state.scrollX, state.scrollY] };
      complete();
    },
  };
}

test("touch setup accepts an already topmost reader without a saved history position", async () => {
  const fixture = scrollFixture(0, null);
  await settleStackedTouchScroll(fixture.page);
  assert.equal(fixture.state.history.state, null);
  assert.equal(fixture.state.frames, 2);
  assert.equal(fixture.state.scrollY, 0);
});

test("touch setup waits for pending scroll persistence before settling the renderer", async () => {
  const fixture = scrollFixture(922, { readerScroll: [0, 922] });
  let settled = false;
  const pending = settleStackedTouchScroll(fixture.page).then(() => {
    settled = true;
  });
  await fixture.waiting;
  assert.equal(fixture.state.scrollY, 0);
  assert.equal(fixture.state.frames, 0);
  assert.equal(settled, false);
  fixture.save();
  await pending;
  assert.equal(settled, true);
  assert.equal(fixture.state.frames, 2);
});

test("reader artifacts allocate distinct owner-only directories for concurrent local runtimes", async (t) => {
  const directories = [];
  t.after(async () => {
    await Promise.all(
      directories.map((directory) => rm(directory, { recursive: true, force: true })),
    );
    for (const directory of directories) await assert.rejects(stat(directory), { code: "ENOENT" });
  });
  await Promise.all(
    ["web", "chrome", "edge", "web"].map(async (runtime) => {
      directories.push(await createReaderArtifactDirectory(runtime, { ci: false }));
    }),
  );
  assert.equal(new Set(directories).size, 4);
  for (const directory of directories) {
    assert.equal(path.dirname(directory), tmpdir());
    assert.equal((await stat(directory)).mode & 0o777, 0o700);
    const evidence = path.join(directory, "observations.json");
    await writeFile(evidence, '{"passed":true}\n', { mode: 0o600 });
    assert.deepEqual(JSON.parse(await readFile(evidence, "utf8")), { passed: true });
  }
});

test("CI artifact directories remain random and private under the uploaded reports tree", async (t) => {
  const directory = await createReaderArtifactDirectory("edge", { ci: true });
  t.after(() => rm(directory, { recursive: true, force: true }));
  assert.equal(path.dirname(directory), path.join(root, "reports", "reader-artifacts"));
  assert.match(path.basename(directory), /^devfeed-reader-edge-.+$/);
  assert.equal((await stat(directory)).mode & 0o777, 0o700);
});

test("an output override is a parent for private random children and cannot select an existing evidence file", async (t) => {
  const parent = await mkdtemp(path.join(tmpdir(), "devfeed-reader-artifact-test-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const requested = path.join(parent, "requested-output");
  await mkdir(requested, { mode: 0o700 });
  await chmod(requested, 0o775);
  const existing = path.join(requested, "web.json");
  await writeFile(existing, "previous unrelated evidence", { mode: 0o600 });
  const directories = await Promise.all(
    ["web", "web"].map((runtime) =>
      createReaderArtifactDirectory(runtime, { parentDirectory: requested, ci: false }),
    ),
  );
  assert.notEqual(directories[0], directories[1]);
  for (const directory of directories) {
    assert.equal(path.dirname(directory), requested);
    assert.equal((await stat(directory)).mode & 0o777, 0o700);
    await writeFile(path.join(directory, "web.json"), "new run", { mode: 0o600 });
  }
  assert.equal((await stat(requested)).mode & 0o777, 0o775);
  assert.equal(await readFile(existing, "utf8"), "previous unrelated evidence");
});

test("unsupported artifact runtimes and invalid parents are rejected before allocation", async (t) => {
  const parent = await mkdtemp(path.join(tmpdir(), "devfeed-reader-artifact-invalid-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const requested = path.join(parent, "must-not-exist");
  for (const runtime of [undefined, "../web", "firefox", "WEB"])
    await assert.rejects(
      createReaderArtifactDirectory(runtime, { parentDirectory: requested }),
      /Unsupported reader artifact runtime/,
    );
  for (const parentDirectory of [null, "", 1])
    await assert.rejects(
      createReaderArtifactDirectory("web", { parentDirectory }),
      /directory path/,
    );
  await assert.rejects(stat(requested), { code: "ENOENT" });
});

test("an externally writable output parent is rejected before any evidence directory is allocated", async (t) => {
  const parent = await mkdtemp(path.join(tmpdir(), "devfeed-reader-artifact-permissions-"));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const requested = path.join(parent, "untrusted");
  await mkdir(requested);
  await chmod(requested, 0o777);
  await assert.rejects(
    createReaderArtifactDirectory("web", { parentDirectory: requested, ci: false }),
    /must not be owned by another user or world-writable/,
  );
  assert.deepEqual(await readdir(requested), []);
});

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
  for (const mode of [
    "--search-context",
    "--link-rel",
    "--attribution",
    "--compact-feed",
    "--header-expiry",
  ]) {
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

test("placement readiness waits for the application instead of accepting an edge 502", async () => {
  let requests = 0;
  const server = createServer((_request, response) => {
    response.writeHead(++requests === 1 ? 502 : 200);
    response.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    assert.equal(
      await waitForPlacementServer(`http://127.0.0.1:${server.address().port}`, {
        attempts: 3,
        delayMs: 0,
      }),
      true,
    );
    assert.equal(requests, 2);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("placement readiness retries a connection that never returns a response", async () => {
  let requests = 0;
  const server = createServer((_request, response) => {
    if (++requests === 1) return;
    response.writeHead(200);
    response.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    assert.equal(
      await waitForPlacementServer(`http://127.0.0.1:${server.address().port}`, {
        attempts: 3,
        delayMs: 0,
        requestTimeoutMs: 250,
      }),
      true,
    );
    assert.equal(requests, 2);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("placement delivery stays blocked until the reading observation completes", async () => {
  const delivery = { pending: null };
  const entered = Promise.withResolvers();
  const complete = Promise.withResolvers();
  const value = { y: 120 };
  let released = false;
  let pending;
  const observation = withPlacementDeliveryHeld(delivery, async () => {
    pending = delivery.pending.then(() => {
      released = true;
    });
    entered.resolve();
    await complete.promise;
    return value;
  });
  await entered.promise;
  assert.equal(released, false);
  assert.ok(delivery.pending);
  complete.resolve();
  assert.equal(await observation, value);
  await pending;
  assert.equal(released, true);
  assert.equal(delivery.pending, null);
});

test("placement stage deadlines identify browser operations that never resolve", async () => {
  await assert.rejects(
    runPlacementStage("web: placement 429 response", () => new Promise(() => {}), {
      timeoutMs: 20,
    }),
    /Placement stage web: placement 429 response timed out after 20ms/,
  );
});

test("placement stage boundaries retain successful values and original assertion errors", async () => {
  const value = { status: 429 };
  assert.equal(await runPlacementStage("web: complete", async () => value), value);
  const original = new assert.AssertionError({ message: "Original placement assertion" });
  await assert.rejects(
    runPlacementStage("web: disclosure", async () => {
      throw original;
    }),
    (error) => error === original,
  );
});

test("placement assertion failures release delivery and close active fixture connections", () => {
  const fixtureModule = new URL("../apps/web/tests/browser/partner-fixture.mjs", import.meta.url)
    .href;
  const result = spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "--eval",
      `
        import assert from "node:assert/strict";
        import { createServer, get } from "node:http";
        const { closePlacementFixture, withPlacementDeliveryHeld } = await import(${JSON.stringify(fixtureModule)});
        const delivery = { pending: null };
        const held = Promise.withResolvers();
        const released = Promise.withResolvers();
        const active = Promise.withResolvers();
        const server = createServer(async (request, response) => {
          if (request.url === "/held") {
            held.resolve();
            await delivery.pending;
            response.end("released");
            released.resolve();
          } else {
            active.resolve();
            await new Promise(() => {});
          }
        });
        await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
        const request = (path) => {
          get({ hostname: "127.0.0.1", port: server.address().port, path }, (response) => {
            response.resume();
          }).on("error", () => {});
        };
        const original = new Error("Controlled placement failure");
        let failure;
        try {
          await withPlacementDeliveryHeld(delivery, async () => {
            request("/held");
            await held.promise;
            throw original;
          });
        } catch (error) {
          failure = error;
        } finally {
          await released.promise;
          assert.equal(delivery.pending, null);
          console.log("gated request released");
          request("/active");
          await active.promise;
          await closePlacementFixture(server);
          console.log("active connection closed");
        }
        assert.equal(failure, original);
        throw failure;
      `,
    ],
    { cwd: root, encoding: "utf8", timeout: 5000 },
  );
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  assert.equal(result.status, 1);
  assert.deepEqual(result.stdout.trim().split("\n"), [
    "gated request released",
    "active connection closed",
  ]);
  assert.match(result.stderr, /^Error: Controlled placement failure$/m);
  assert.doesNotMatch(result.stderr, /AssertionError|ETIMEDOUT/);
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

import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  propertySettings,
  propertyOptions,
  commandOptions,
} from "../scripts/ci/property-config.mjs";
import { checkPropertyReport, propertySuites } from "../scripts/ci/property-report.mjs";

function report() {
  return {
    success: true,
    numTotalTests: 15,
    numPassedTests: 15,
    testResults: Object.entries(propertySuites).map(([name, count]) => ({
      name: `/workspace/apps/web/tests/${name}`,
      status: "passed",
      assertionResults: Array.from({ length: count }, (_, index) => ({
        fullName: `property ${index}`,
        status: "passed",
      })),
    })),
  };
}

test("nightly expands generated cases and command lengths; ordinary tests stay bounded", () => {
  const unit = propertySettings({});
  const pr = propertySettings({ DEVFEED_PROPERTY_MODE: "pr" });
  const nightly = propertySettings({ DEVFEED_PROPERTY_MODE: "nightly" });
  assert.ok(unit.modelRuns < pr.modelRuns);
  for (const key of ["runs", "modelRuns", "maxCommands"]) assert.ok(pr[key] < nightly[key]);
});

test("seed and both replay paths are preserved, including zero and negative seeds", () => {
  for (const seed of ["0", "-2147483648", "2147483647"]) {
    const settings = propertySettings({
      DEVFEED_FUZZ_SEED: seed,
      DEVFEED_FUZZ_PATH: "0:1:2",
      DEVFEED_FUZZ_REPLAY_PATH: "ABC:DEF",
    });
    assert.equal(settings.seed, Number(seed));
    assert.equal(settings.path, "0:1:2");
    assert.equal(settings.replayPath, "ABC:DEF");
  }
});

test("bad budgets, seeds and incomplete replay settings fail instead of weakening coverage", () => {
  for (const env of [
    { DEVFEED_PROPERTY_MODE: "unknown" },
    ...["", "NaN", "1.5", "2147483648", "-2147483649"].map((DEVFEED_FUZZ_SEED) => ({
      DEVFEED_FUZZ_SEED,
    })),
    { DEVFEED_FUZZ_PATH: "0:1" },
    { DEVFEED_FUZZ_REPLAY_PATH: "ABC" },
    { DEVFEED_FUZZ_SEED: "0", DEVFEED_FUZZ_PATH: "invalid" },
  ])
    assert.throws(() => propertySettings(env));
});

test("the complete passing suite satisfies the report gate", () => {
  assert.doesNotThrow(() => checkPropertyReport(report()));
});

test("missing, empty, duplicate and unexpected suites fail the report gate", () => {
  for (const modify of [
    (value) => {
      value.testResults = [];
    },
    (value) => {
      value.testResults.pop();
    },
    (value) => {
      value.testResults[1] = value.testResults[0];
    },
    (value) => {
      value.testResults[0].name = "unexpected.test.ts";
    },
    (value) => {
      value.numTotalTests = 0;
    },
    (value) => {
      value.numPassedTests = 0;
    },
  ]) {
    const value = report();
    modify(value);
    assert.throws(() => checkPropertyReport(value));
  }
});

test("failed, skipped and missing properties cannot produce a green gate", () => {
  for (const modify of [
    (value) => {
      value.success = false;
    },
    (value) => {
      value.testResults[0].status = "failed";
    },
    ...["failed", "pending", "skipped", "todo"].map((status) => (value) => {
      value.testResults[0].assertionResults[0].status = status;
    }),
    (value) => {
      value.testResults[0].assertionResults.pop();
    },
    (value) => {
      value.testResults[0].assertionResults[0].fullName = "";
    },
    (value) => {
      value.testResults[0].assertionResults[1] = value.testResults[0].assertionResults[0];
    },
  ]) {
    const value = report();
    modify(value);
    assert.throws(() => checkPropertyReport(value));
  }
});

test("empty and nonboolean report success cannot pass the gate", () => {
  for (const value of [null, undefined, {}, { ...report(), success: "true" }])
    assert.throws(() => checkPropertyReport(value), /Expected 15 passing properties/);
});

test("fast-check options keep pure and stateful budgets separate and preserve replay", () => {
  const keys = [
    "DEVFEED_PROPERTY_MODE",
    "DEVFEED_FUZZ_SEED",
    "DEVFEED_FUZZ_PATH",
    "DEVFEED_FUZZ_REPLAY_PATH",
  ];
  const original = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) delete process.env[key];
    assert.deepEqual(propertyOptions(), { numRuns: 2000 });
    assert.deepEqual(propertyOptions(true), { numRuns: 20 });
    assert.deepEqual(commandOptions(), { maxCommands: 20, size: "max" });
    Object.assign(process.env, {
      DEVFEED_PROPERTY_MODE: "pr",
      DEVFEED_FUZZ_SEED: "0",
      DEVFEED_FUZZ_PATH: "0:1",
      DEVFEED_FUZZ_REPLAY_PATH: "ABC:DEF",
    });
    assert.deepEqual(propertyOptions(), { numRuns: 1000, seed: 0, path: "0:1" });
    assert.deepEqual(propertyOptions(true), { numRuns: 60, seed: 0, path: "0:1" });
    assert.deepEqual(commandOptions(), { maxCommands: 30, size: "max", replayPath: "ABC:DEF" });
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("the CI entrypoint rejects invalid budgets and partial replay runs", () => {
  for (const [env, expected] of [
    [{ DEVFEED_PROPERTY_MODE: "unknown" }, /Invalid property mode/],
    [{ DEVFEED_FUZZ_SEED: "NaN" }, /signed 32-bit/],
    [{ DEVFEED_FUZZ_SEED: "1", DEVFEED_FUZZ_PATH: "0:1" }, /complete suite/],
    [{ DEVFEED_FUZZ_SEED: "1", DEVFEED_FUZZ_REPLAY_PATH: "ABC:DEF" }, /complete suite/],
  ]) {
    const result = spawnSync(process.execPath, ["scripts/ci/property-tests.mjs"], {
      cwd: fileURLToPath(new URL("../", import.meta.url)),
      env: { ...process.env, DEVFEED_PROPERTY_MODE: "pr", ...env },
      encoding: "utf8",
      timeout: 10_000,
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, expected);
  }
});

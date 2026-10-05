import { randomInt } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, readFile, rm, writeFile, appendFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { propertySettings } from "./property-config.mjs";
import { checkPropertyReport, propertySuites } from "./property-report.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const environment = {
  ...process.env,
  DEVFEED_PROPERTY_MODE: process.env.DEVFEED_PROPERTY_MODE ?? "pr",
  DEVFEED_FUZZ_SEED: process.env.DEVFEED_FUZZ_SEED ?? String(randomInt(-(2 ** 31), 2 ** 31)),
};
// Measure the controller, rather than raw V8 snapshots from Vitest and its workers.
delete environment.NODE_V8_COVERAGE;
const settings = propertySettings(environment);
if (settings.path !== undefined || settings.replayPath !== undefined)
  throw new Error("Replay individual properties with web:test -t; CI requires the complete suite");
const output = path.join(root, "reports/property");
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await writeFile(
  path.join(output, "settings.json"),
  JSON.stringify({ ...settings, suites: propertySuites }, null, 2) + "\n",
);
const log = [];
console.log(
  `Property mode=${settings.mode}, seed=${settings.seed}, cases=${settings.runs}, model cases=${settings.modelRuns}, commands<=${settings.maxCommands}`,
);
const code = await new Promise((resolve, reject) => {
  const child = spawn(
    process.execPath,
    [
      path.join(root, "node_modules/vitest/vitest.mjs"),
      "run",
      ...Object.keys(propertySuites).map((suite) => `tests/${suite}`),
      "--reporter=default",
      "--reporter=json",
      "--reporter=junit",
      `--outputFile.json=${output}/vitest.json`,
      `--outputFile.junit=${output}/junit.xml`,
    ],
    { cwd: path.join(root, "apps/web"), env: environment, stdio: ["ignore", "pipe", "pipe"] },
  );
  for (const stream of [child.stdout, child.stderr])
    stream.on("data", (chunk) => {
      log.push(chunk.toString());
      (stream === child.stdout ? process.stdout : process.stderr).write(chunk);
    });
  child.on("error", reject);
  child.on("close", resolve);
});
await writeFile(path.join(output, "run.log"), log.join(""));
let failure;
try {
  if (code !== 0) throw new Error(`Property tests exited with ${code}`);
  checkPropertyReport(JSON.parse(await readFile(path.join(output, "vitest.json"), "utf8")));
} catch (error) {
  failure = error;
}
const summary = [
  "## Property tests",
  "",
  `Result: **${failure ? "failed" : "passed"}**. Mode: **${settings.mode}**. Seed: \`${settings.seed}\`.`,
  "",
  `Each pure property: ${settings.runs} cases. Each stateful property: ${settings.modelRuns} cases. Command models allow up to ${settings.maxCommands} actions per run.`,
  "",
  "| Suite | Properties |",
  "| --- | ---: |",
  ...Object.entries(propertySuites).map(([name, count]) => `| ${name} | ${count} |`),
  "",
  "The artifact includes settings, JSON/JUnit reports and the full failure log with shrunk counterexamples, paths and command replay paths.",
  "",
  "Replay the entire run:",
  "```sh",
  `DEVFEED_PROPERTY_MODE=${settings.mode} DEVFEED_FUZZ_SEED=${settings.seed} npm run ci:property`,
  "```",
  "",
  "Replay a failure with the same mode/seed, set `DEVFEED_FUZZ_PATH` to the logged shrinking path and `DEVFEED_FUZZ_REPLAY_PATH` to the replayPath for command models, then select that file and property with `npm run web:test -- tests/<file> -t '<property>'`.",
  "",
].join("\n");
await writeFile(path.join(output, "summary.md"), summary);
if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY, summary);
if (failure) {
  console.error(failure);
  process.exitCode = 1;
}

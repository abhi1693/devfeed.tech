import { declareFactoryPlugin, PluginKind } from "@stryker-mutator/api/plugin";
import { strykerPlugins as vitestPlugins } from "@stryker-mutator/vitest-runner";
import { suiteCoverage } from "./mutation.mjs";

const original = vitestPlugins.find((plugin) => plugin.name === "vitest").factory;
export function withSuiteCoverage(runner) {
  const dryRun = runner.dryRun.bind(runner);
  // Stryker 10's Vitest runner always returns per-test coverage, ignoring coverageAnalysis.
  // Suite coverage avoids its test-name filtering mismatch with nested Vitest 5 suites.
  runner.dryRun = async (options) => suiteCoverage(await dryRun(options));
  return runner;
}
function factory(...args) {
  return withSuiteCoverage(original(...args));
}
factory.inject = original.inject;

export const strykerPlugins = [
  declareFactoryPlugin(PluginKind.TestRunner, "vitest-suite", factory),
];
export { strykerValidationSchema } from "@stryker-mutator/vitest-runner";

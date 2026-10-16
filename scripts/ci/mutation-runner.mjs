import { declareFactoryPlugin, PluginKind } from "@stryker-mutator/api/plugin";
import { DryRunStatus, MutantRunStatus } from "@stryker-mutator/api/test-runner";
import { strykerPlugins as vitestPlugins } from "@stryker-mutator/vitest-runner";
import { suiteCoverage } from "./mutation.mjs";

const original = vitestPlugins.find((plugin) => plugin.name === "vitest").factory;

function suiteError(runner) {
  const errors = [];
  function collect(suite) {
    if (suite.result?.state === "fail")
      for (const error of suite.result.errors ?? [])
        errors.push(`${suite.filepath ?? suite.name}: ${error?.message ?? String(error)}`);
    for (const task of suite.tasks ?? []) if (task.type === "suite") collect(task);
  }
  for (const file of runner.ctx?.state.getFiles() ?? []) collect(file);
  return errors.join("\n");
}

export function withSuiteCoverage(runner) {
  const dryRun = runner.dryRun.bind(runner);
  // Stryker 10's Vitest runner always returns per-test coverage, ignoring coverageAnalysis.
  // Suite coverage avoids its test-name filtering mismatch with nested Vitest 5 suites.
  runner.dryRun = async (options) => {
    const result = await dryRun(options);
    const error = suiteError(runner);
    if (result.status === DryRunStatus.Complete && error)
      return { status: DryRunStatus.Error, errorMessage: error };
    return suiteCoverage(result);
  };
  // The upstream runner inspects leaf tests and misses suites that failed to import.
  // A passing neighbor must not turn a collection failure into a surviving mutant.
  if (runner.mutantRun) {
    const mutantRun = runner.mutantRun.bind(runner);
    runner.mutantRun = async (options) => {
      const result = await mutantRun(options);
      const error = suiteError(runner);
      return result.status === MutantRunStatus.Survived && error
        ? { status: MutantRunStatus.Error, errorMessage: error }
        : result;
    };
  }
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

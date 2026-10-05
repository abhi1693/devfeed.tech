import { readFileSync } from "node:fs";

const policy = JSON.parse(readFileSync(new URL("./mutation-policy.json", import.meta.url), "utf8"));
const group = process.env.MUTATION_GROUP;
if (!Object.hasOwn(policy, group)) throw new Error("Select a configured mutation group");

export default {
  testRunner: "vitest-suite",
  plugins: ["./scripts/ci/mutation-runner.mjs"],
  // Each group already has a focused suite, including shared workspace modules.
  vitest: { configFile: "scripts/ci/mutation.vitest.config.ts", related: false },
  // Vitest 5 uses " > " in nested test names; Stryker 10's per-test filter uses spaces.
  // Run the focused suite for each covered mutant until the runner supports that format.
  coverageAnalysis: "all",
  mutate: Object.keys(policy[group].files),
  reporters: ["clear-text", "html", "json"],
  htmlReporter: { fileName: `reports/mutation/${group}/index.html` },
  jsonReporter: { fileName: `reports/mutation/${group}/mutation.json` },
  tempDirName: `reports/mutation/tmp-${group}`,
  cleanTempDir: "always",
  concurrency: 2,
  timeoutMS: 2000,
  dryRunTimeoutMinutes: 3,
  thresholds: { high: 95, low: 80, break: Math.min(...Object.values(policy[group].files)) },
  ignorePatterns: [
    "**",
    "!package.json",
    "!package-lock.json",
    "!apps/web/{package.json,vitest.config.ts,src/**,tests/**,public/**}",
    "!packages/{telemetry,ui,theme}/**",
    "!scripts/ci/*mutation*",
    ...(policy[group].dependencies ?? []).map((file) => `!${file}`),
    "**/.next/**",
    "**/node_modules/**",
    "**/dist/**",
  ],
};

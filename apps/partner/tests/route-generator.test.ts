import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

it("checks concrete partner API generation and its contract/security regressions", () => {
  const suite = fileURLToPath(
    new URL("../../../tests/partner-route-generator.test.mjs", import.meta.url),
  );
  const result = spawnSync(process.execPath, ["--test", suite], {
    encoding: "utf8",
    timeout: 20_000,
  });
  expect(result.status, result.stdout + result.stderr).toBe(0);
}, 25_000);

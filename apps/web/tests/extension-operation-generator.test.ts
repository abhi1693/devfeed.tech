import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

it("compiles declared extension operations against backend policies and public Next routes", () => {
  const suite = fileURLToPath(
    new URL("../../../tests/extension-operation-generator.test.mjs", import.meta.url),
  );
  const result = spawnSync(process.execPath, ["--test", suite], {
    encoding: "utf8",
    timeout: 20_000,
  });
  expect(result.status, result.stdout + result.stderr).toBe(0);
}, 25_000);

import { defineConfig } from "vitest/config";
import path from "node:path";
import { readFileSync } from "node:fs";
import web from "../../apps/web/vitest.config";

const root = path.resolve(import.meta.dirname, "../..");
const policy = JSON.parse(readFileSync(new URL("./mutation-policy.json", import.meta.url), "utf8"));
const group = process.env.MUTATION_GROUP;
if (!group || !Object.hasOwn(policy, group)) throw new Error("Select a configured mutation group");
export default defineConfig({
  ...web,
  root: path.join(root, "apps/web"),
  test: {
    ...web.test,
    include: policy[group].tests.map((name: string) => `tests/${name}.test.{ts,tsx}`),
    setupFiles: [
      path.join(root, "apps/web/tests/setup.ts"),
      path.join(root, "scripts/ci/mutation-setup.ts"),
    ],
  },
  resolve: {
    ...web.resolve,
    alias: {
      ...web.resolve?.alias,
      // Workspace symlinks otherwise resolve back to the original, unmutated package.
      ...Object.fromEntries(
        ["privacy", "session", "receiver", "server", "propagation", "browser"].map((name) => [
          `@devfeed/telemetry/${name}`,
          path.join(root, `packages/telemetry/src/${name}.${name === "browser" ? "tsx" : "ts"}`),
        ]),
      ),
    },
  },
});

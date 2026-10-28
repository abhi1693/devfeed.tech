import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { checkExtensionRouteAnalytics } from "../../../scripts/testing/route-analytics.mjs";

const bundle = await build({
  entryPoints: ["apps/web/src/lib/extension-analytics.ts"],
  bundle: true,
  write: false,
  format: "esm",
});
const { extensionPage } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

test("route telemetry waits for startup and distinguishes preview headings from cards", async () => {
  const events = [];
  let startupDelivered = false;
  const emit = (path) =>
    events.push({ event: { name: "page_view", params: { page_path: extensionPage(path) } } });
  const startup = () => {
    if (startupDelivered) return;
    startupDelivered = true;
    emit("/");
  };
  await checkExtensionRouteAnalytics(
    {
      bringToFront: async () => {},
      waitForTimeout: async () => startup(),
      goto: async (destination) => {
        // A pending homepage envelope can arrive after navigation has begun.
        startup();
        emit(new URL(destination).hash.slice(1));
      },
      getByRole: (role, options) => {
        assert.equal(role, "heading");
        if (options.name === "Shared reader components in every new tab")
          assert.equal(options.level, 1, "wait for the preview rather than background cards");
        if (options.name === "Sign in to connect your agent")
          assert.equal(options.level, undefined, "the account gate uses an h2 heading");
        return { waitFor: async () => {} };
      },
    },
    (path) => `https://extension.test/#${path}`,
    events,
  );
  assert.equal(events.length, 12);
});

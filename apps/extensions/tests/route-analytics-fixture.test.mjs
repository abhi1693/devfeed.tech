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

test("route telemetry assertions wait for the delayed initial page view", async () => {
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
      getByRole: () => ({ waitFor: async () => {} }),
    },
    (path) => `https://extension.test/#${path}`,
    events,
  );
  assert.equal(events.length, 12);
});

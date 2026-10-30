import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { readerRouteUrl } from "./article-view.mjs";
import { columnsFeedSelections, installColumnsFeedFixture } from "./columns-feed-fixture.mjs";

/** Public feeds stay independent before the first authenticated Columns visit. */
export async function checkDeferredColumns(page, target, output) {
  const fixture = await installColumnsFeedFixture(page, { signedIn: true });
  fixture.setSettings({ view: "cards" });
  const saved = columnsFeedSelections.slice(0, 2);
  fixture.setColumns(saved);
  fixture.scopedViews.store.write("my_feed", { view: "columns" });
  const requests = [];
  const record = (request) => {
    if (/\/api\/v1\/user\/settings\/columns(?:\?|$)/.test(request.url()))
      requests.push({ method: request.method(), url: request.url() });
  };
  page.on("request", record);
  const workspace = page.getByRole("region", { name: "Columns workspace", exact: true });
  const identifiers = () =>
    workspace
      .locator("[data-column-id]")
      .evaluateAll((nodes) => nodes.map((node) => node.dataset.columnId));
  try {
    for (const path of ["/latest", "/trending"]) {
      await page.goto(readerRouteUrl(target, path));
      await page.locator(".article-card").first().waitFor();
      assert.equal(await workspace.count(), 0, "Public routes retain their own feed");
      assert.equal(requests.length, 0, "Public routes never initialize private Columns");
    }
    await page.goto(readerRouteUrl(target, "/"));
    await workspace.waitFor();
    await page.waitForFunction(
      (count) => document.querySelectorAll("[data-column-id]").length === count,
      saved.length,
    );
    assert.deepEqual(
      await identifiers(),
      saved.map(({ id }) => id),
    );
    assert.ok(requests.length > 0, "The eligible workspace reads its saved columns");
    assert.ok(
      requests.every(
        ({ method, url }) =>
          method === "GET" && new URL(url).searchParams.get("workspace") === "my_feed",
      ),
    );
    assert.deepEqual(
      fixture.scopedViews.writes,
      [],
      "Deferred initialization never rewrites layouts",
    );
    assert.deepEqual(fixture.writes, [], "Saved reader preferences stay unchanged");
    await mkdir(dirname(output), { recursive: true });
    await page.screenshot({ path: `${output}.png`, animations: "disabled" });
    await writeFile(
      `${output}.json`,
      `${JSON.stringify({ passed: true, publicRoutes: ["/latest", "/trending"], columns: await identifiers(), privateReads: requests.length }, null, 2)}\n`,
    );
  } finally {
    page.off("request", record);
    await fixture.cleanup();
  }
}

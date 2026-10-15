import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { generatePageRoutes, pageRouteTemplates } from "../scripts/generate_page_routes.mjs";

test("generated catalogs stay aligned with registered Next routes and shared route descriptors", async () => {
  await generatePageRoutes({ check: true });
});

test("new concrete pages, endpoints, and dynamic parameters enter the Next descriptor graph", async () => {
  const directory = await mkdtemp(join(tmpdir(), "devfeed-page-routes-"));
  try {
    for (const file of [
      "page.tsx",
      "(workspace)/products/[productId]/edit/page.tsx",
      "api/v1/products/[productId]/route.ts",
      "_private/page.tsx",
      "@modal/(.)products/[productId]/page.tsx",
    ]) {
      await mkdir(join(directory, file, ".."), { recursive: true });
      await writeFile(join(directory, file), "export default function Page() {}\n");
    }
    assert.deepEqual(await pageRouteTemplates(pathToFileURL(`${directory}/`)), [
      { route: "/", kind: "page" },
      { route: "/api/v1/products/[productId]", kind: "endpoint" },
      { route: "/products/[productId]/edit", kind: "page" },
    ]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

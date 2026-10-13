import assert from "node:assert/strict";
import { creativeProduct } from "../../../scripts/testing/native-ad-formats.mjs";

export function createManualProductFixture() {
  const products = [];
  const writes = [];
  const imported = new Set();
  return {
    products,
    writes,
    imported,
    async handle(request, response, url, accounts) {
      const match = /^\/v1\/partner\/accounts\/([a-f0-9-]+)\/products(?:\/([a-f0-9-]+))?$/i.exec(
        url.pathname,
      );
      if (!match) {
        if (url.pathname.endsWith("/creative-preview")) {
          const product = products.find((item) => item.id === url.searchParams.get("product_id"));
          if (product) {
            response.end(
              JSON.stringify({
                product: {
                  ...creativeProduct(product),
                  ...product,
                  logo_url: null,
                  image_url: null,
                  ad_image_status: "default",
                  listing_url: null,
                },
              }),
            );
            return true;
          }
        }
        return false;
      }
      const account = accounts.find((item) => item.id === match[1]);
      if (!account) {
        response.writeHead(404);
        response.end("{}");
        return true;
      }
      let product = products.find((item) => item.account_id === account.id && item.id === match[2]);
      if (match[2] && !product) return false;
      if (request.method === "GET" && product) {
        response.end(JSON.stringify(product));
        return true;
      }
      if (request.method === "DELETE" && product) {
        assert.equal(request.headers["x-csrf-token"], "test-csrf");
        product.active = imported.has(product.id);
        product.manual_active = false;
        writes.push({ method: "DELETE", id: product.id });
        response.writeHead(204);
        response.end();
        return true;
      }
      if (!["POST", "PATCH"].includes(request.method)) {
        response.writeHead(405);
        response.end("{}");
        return true;
      }
      assert.equal(request.headers["x-csrf-token"], "test-csrf");
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const body = JSON.parse(raw);
      writes.push({ method: request.method, body });
      if (request.method === "POST") {
        assert.equal(match[2], undefined);
        assert.deepEqual(Object.keys(body).sort(), [
          "description",
          "name",
          "pricing",
          "product_url",
        ]);
        product = {
          id: "77777777-7777-4777-8777-777777777777",
          account_id: account.id,
          account_name: account.name,
          ...body,
          technologies: [],
          status: "pending",
          assessment: { state: "waiting", reason: "" },
          revision: 1,
          source: "manual",
          manual_active: true,
          editable: true,
          active: true,
          excluded: false,
          globally_excluded: false,
          commercial_approved: false,
          approval_source: null,
          ad_formats: null,
          promotion_surfaces: null,
          ad_image_url: null,
        };
        products.push(product);
        response.writeHead(201);
        response.end(JSON.stringify(product));
        return true;
      }
      assert.equal(body.expected_revision, product.revision);
      const changes = { ...body };
      delete changes.expected_revision;
      Object.assign(product, changes);
      if (Object.hasOwn(changes, "active")) {
        product.manual_active = changes.active;
        product.active = changes.active || imported.has(product.id);
      }
      product.revision += 1;
      response.end(JSON.stringify(product));
      return true;
    },
  };
}

export async function assertManualProductJourneys({ page, origin, account, fixture, artifactDir }) {
  await page.goto(origin + "/products");
  await page.getByRole("link", { name: "Add product", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/products/new");
  await page.getByRole("heading", { name: "Add product", level: 1 }).waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.getByRole("combobox", { name: /connection/i }).count(), 0);
  await page.getByRole("combobox", { name: "Partner account", exact: true }).click();
  await page.getByRole("option", { name: account.name, exact: true }).click();
  await page.getByRole("textbox", { name: "Product name", exact: true }).fill("BuildSignal");
  await page
    .getByRole("textbox", { name: "Website URL", exact: true })
    .fill("https://buildsignal.example/");
  await page
    .getByRole("textbox", { name: "Description", exact: true })
    .fill("Monitor failed builds and receive actionable CI diagnostics for your team.");
  await page.getByRole("combobox", { name: "Pricing", exact: true }).click();
  await page.getByRole("option", { name: "Freemium", exact: true }).click();
  await page.screenshot({ path: artifactDir + "/product-create-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: artifactDir + "/product-create-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Add product", exact: true }).click();
  await page.waitForURL((url) => /^\/products\/[a-f0-9-]{36}$/.test(url.pathname));
  const product = fixture.products.at(-1);
  const bookmark = page.url();
  await page.getByRole("heading", { name: "BuildSignal", exact: true, level: 1 }).waitFor();
  await page.getByText("Added manually", { exact: true }).waitFor();
  await page.getByText("In review", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Edit product", exact: true }).click();
  await page.waitForURL((url) => url.pathname.endsWith("/edit"));
  await page.getByRole("textbox", { name: "Product name", exact: true }).fill("Unsaved name");
  await page.getByRole("link", { name: "Cancel", exact: true }).click();
  await page.getByRole("heading", { name: "BuildSignal", exact: true, level: 1 }).waitFor();
  assert.equal(product.name, "BuildSignal");
  await page.getByRole("link", { name: "Edit product", exact: true }).click();
  await page.getByRole("textbox", { name: "Product name", exact: true }).fill("BuildSignal Pro");
  await page.getByRole("button", { name: "Save product", exact: true }).click();
  await page.getByRole("heading", { name: "BuildSignal Pro", exact: true, level: 1 }).waitFor();
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.screenshot({
    path: artifactDir + "/manual-product-detail-desktop.png",
    fullPage: true,
  });
  await page.goto(origin + "/products");
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("link", { name: "BuildSignal Pro", exact: true }) });
  await row.getByText("Manual", { exact: true }).waitFor();
  await row.getByRole("link", { name: "BuildSignal Pro", exact: true }).click();
  await page.getByRole("link", { name: "Archive product", exact: true }).click();
  await page.getByRole("heading", { name: "Archive this product?", exact: true }).waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  await page.screenshot({
    path: artifactDir + "/manual-product-archive-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Archive product", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/products");
  assert.equal(product.active, false);
  await page.goto(bookmark);
  await page.getByRole("heading", { name: "Archived product", exact: true }).waitFor();
  await page.getByRole("button", { name: "Restore product", exact: true }).click();
  await page.getByRole("link", { name: "Edit product", exact: true }).waitFor();
  assert.equal(product.active, true);
  fixture.imported.add(product.id);
  await page.getByRole("link", { name: "Archive product", exact: true }).click();
  await page.getByRole("button", { name: "Archive product", exact: true }).click();
  await page.waitForURL((url) => url.pathname === "/products");
  assert.equal(product.active, true, "An active imported source keeps the product available");
  assert.equal(product.manual_active, false);
  await page.goto(bookmark);
  await page.getByRole("heading", { name: "Manually added entry archived", exact: true }).waitFor();
  await page.getByRole("link", { name: "Edit placements", exact: true }).waitFor();
  await page.getByRole("button", { name: "Restore product", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Archive product", exact: true }).count(), 0);
  fixture.imported.delete(product.id);
  product.active = false;
  assert.deepEqual(
    fixture.writes.map((item) => item.method),
    ["POST", "PATCH", "DELETE", "PATCH", "DELETE"],
  );
}

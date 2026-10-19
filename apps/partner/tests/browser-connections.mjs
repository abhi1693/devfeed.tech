import assert from "node:assert/strict";
import { join } from "node:path";
import {
  logoSource,
  managedLogo,
  uploadedLogo,
  connectionLogo,
  readLogoUpload,
  readLogoUrl,
  installConnectionLogoMedia,
  assertManagedConnectionLogo,
  selectConnectionLogo,
  assertConnectionLogoEditor,
  assertInvalidConnectionLogo,
  removeConnectionLogoFromEditedUrl,
  saveConnectionFromEditedLogoUrl,
} from "../../../scripts/testing/connection-logos.mjs";
import { assertSelectMenu } from "../../../scripts/testing/select-menus.mjs";
import { assertConnectorFilterColors } from "../../../scripts/testing/connector-filters.mjs";

export function createConnectionFixture({ account, origin }) {
  assert.match(account.id, /^[a-f0-9-]{36}$/i);
  const records = new Map();
  const archived = new Set();
  const credentials = new Map();
  const runs = new Map();
  const jobReads = [];
  const writes = [];
  const previews = [];
  const logoUploads = [];
  const logoWrites = [];
  let failLogoUpload = false;
  let logoUploadGate = null;
  let sequence = 0;

  function json(response, value, status = 200) {
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(JSON.stringify(value));
  }
  function output(record) {
    return { ...structuredClone(record), credential_configured: credentials.has(record.provider) };
  }
  async function body(request) {
    assert.equal(request.headers["x-csrf-token"], "test-csrf");
    assert.equal(request.headers.origin, origin());
    let raw = "";
    for await (const chunk of request) raw += chunk;
    return JSON.parse(raw);
  }
  async function handle(request, response, url, memberAccounts) {
    const preview =
      /^\/v1\/partner\/accounts\/([^/]+)\/connections(?:\/([^/]+))?\/(connector-response|connector-preview)$/.exec(
        url.pathname,
      );
    if (preview) {
      const [, accountId, provider, kind] = preview;
      const record = provider ? records.get(provider) : undefined;
      if (
        request.method !== "POST" ||
        !memberAccounts.some((item) => item.id === accountId) ||
        (provider && (!record || record.account_id !== accountId || archived.has(provider)))
      ) {
        json(response, {}, 404);
        return true;
      }
      const input = await body(request);
      assert.deepEqual(Object.keys(input.connector.auth).sort(), ["header", "mode"]);
      if (record && input.expected_revision !== record.revision) {
        json(response, { detail: "Connection changed" }, 409);
        return true;
      }
      previews.push({ accountId, provider, kind, input: structuredClone(input) });
      const product = {
        id: "browser-preview-product",
        name: "Preview developer tool",
        display_name: "Alternative developer tool title",
        url: "https://preview-tool.example",
        listing_url: "https://catalog.example/products/browser-preview-product",
        tagline: "A developer product shown only in the import preview.",
        pricing: "free",
        categories: ["Developer Tools", "Productivity"],
      };
      json(
        response,
        kind === "connector-response"
          ? { data: { items: [product], nextCursor: null }, sampled: true }
          : {
              discovered: 1,
              has_next_page: false,
              errors: [],
              products: [
                {
                  external_id: product.id,
                  provider: provider ?? "connector-preview",
                  name: product.name,
                  product_url: product.url,
                  listing_url: product.listing_url,
                  tagline: product.tagline,
                  pricing: product.pricing,
                },
              ],
            },
      );
      return true;
    }
    const scoped =
      /^\/v1\/partner\/accounts\/([^/]+)\/connections(?:\/([^/]+))?(?:\/(actions|jobs|logo))?$/.exec(
        url.pathname,
      );
    const workspace = /^\/v1\/partner\/connections(?:\/([^/]+))?(?:\/(jobs))?$/.exec(url.pathname);
    if (!scoped && !workspace) return false;
    const accountId = scoped?.[1] ?? url.searchParams.get("account_id") ?? undefined;
    const provider = scoped?.[2] ?? workspace?.[1];
    const subresource = scoped?.[3] ?? workspace?.[2];
    if (accountId && !memberAccounts.some((item) => item.id === accountId)) {
      json(response, {}, 404);
      return true;
    }
    const record = provider ? records.get(provider) : undefined;
    if (
      provider &&
      (!record ||
        !memberAccounts.some((item) => item.id === record.account_id) ||
        (accountId && record.account_id !== accountId) ||
        archived.has(provider))
    ) {
      json(response, {}, 404);
      return true;
    }
    if (!provider && request.method === "GET") {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 25);
      assert.ok(Number.isInteger(limit) && limit > 0 && limit <= 100);
      const all = [...records.values()].filter(
        (item) =>
          memberAccounts.some((owner) => owner.id === item.account_id) &&
          (!accountId || item.account_id === accountId) &&
          !archived.has(item.provider) &&
          `${item.name} ${item.provider} ${item.account_name}`
            .toLowerCase()
            .includes((url.searchParams.get("q") ?? "").toLowerCase()),
      );
      json(response, {
        items: all.slice(offset, offset + limit).map(output),
        total: all.length,
        limit,
        offset,
        credentials_available: true,
      });
      return true;
    }
    if (request.method === "GET" && subresource === "jobs") {
      const items = runs.get(provider) ?? [];
      jobReads.push(provider);
      json(response, { items, total: items.length, limit: 10, offset: 0 });
      return true;
    }
    if (provider && request.method === "GET") {
      json(response, output(record));
      return true;
    }
    if (subresource === "logo" && request.method === "PUT") {
      const input = await readLogoUrl(request, "test-csrf", origin());
      assert.equal(
        input.expected_revision,
        record.revision,
        "Immediate logo edits use the latest revision",
      );
      logoWrites.push({ provider, ...input });
      Object.assign(record, connectionLogo(input.logo_url), { revision: record.revision + 1 });
      json(response, output(record));
      return true;
    }
    if (subresource === "logo" && request.method === "POST") {
      const input = await readLogoUpload(request, "test-csrf", origin());
      logoUploads.push({ provider, ...input });
      if (logoUploadGate) await logoUploadGate;
      assert.equal(
        input.expected_revision,
        record.revision,
        "Logo upload uses the latest saved revision",
      );
      if (failLogoUpload) {
        failLogoUpload = false;
        json(response, { detail: "Logo storage temporarily unavailable." }, 503);
      } else {
        Object.assign(record, connectionLogo(null, true), { revision: record.revision + 1 });
        json(response, output(record));
      }
      return true;
    }
    const input = await body(request);
    writes.push({ method: request.method, provider, subresource, input: structuredClone(input) });
    if (record && input.expected_revision !== record.revision) {
      json(response, { detail: "Connection changed" }, 409);
      return true;
    }
    if (!provider && request.method === "POST") {
      const owner = memberAccounts.find((item) => item.id === (accountId ?? input.account_id));
      if (!owner) {
        json(response, {}, 404);
        return true;
      }
      sequence += 1;
      const id = `partner-browser-connection-${sequence}`;
      const connector = structuredClone(input.connector);
      assert.deepEqual(Object.keys(connector.auth).sort(), ["header", "mode"]);
      const created = {
        provider: id,
        account_id: owner.id,
        account_name: owner.name,
        account_tier: owner.tier,
        credentials_available: true,
        name: input.name,
        ...connectionLogo(input.logo_url ?? null),
        api_url: `${connector.base_url}${connector.list_path}`,
        connector,
        enabled: input.enabled,
        sync_interval_minutes: input.sync_interval_minutes,
        revision: 1,
        partnership_type: "launch_platform",
        state: "idle",
        last_sync_at: null,
        next_sync_at: input.enabled ? "2026-10-13T08:00:00Z" : null,
        error: null,
        products: 0,
        qualified: 0,
        commercial_approved: 0,
        checking: 0,
        needs_attention: 0,
        excluded: 0,
        ai_enabled: true,
        auth_mode: connector.auth?.mode ?? "none",
        credential_header: connector.auth?.header ?? "X-API-Key",
      };
      records.set(id, created);
      if (input.credential) credentials.set(id, input.credential);
      json(response, output(created), 201);
      return true;
    }
    if (request.method === "PUT" && !subresource) {
      if (Object.hasOwn(input, "logo_url")) Object.assign(record, connectionLogo(input.logo_url));
      record.name = input.name ?? record.name;
      record.enabled = input.enabled;
      record.sync_interval_minutes = input.sync_interval_minutes ?? record.sync_interval_minutes;
      if (input.connector) {
        record.connector = structuredClone(input.connector);
        record.api_url = `${record.connector.base_url}${record.connector.list_path}`;
        record.auth_mode = record.connector.auth?.mode ?? "none";
        record.credential_header = record.connector.auth?.header ?? "X-API-Key";
        if (record.auth_mode === "none") credentials.delete(provider);
      }
      if (input.credential) credentials.set(provider, input.credential);
      record.revision += 1;
      json(response, output(record));
      return true;
    }
    if (request.method === "DELETE" && !subresource) {
      archived.add(provider);
      credentials.delete(provider);
      record.enabled = false;
      record.state = "disconnected";
      record.next_sync_at = null;
      record.revision += 1;
      json(response, output(record));
      return true;
    }
    if (request.method === "POST" && subresource === "actions") {
      assert.ok(["sync", "pause", "resume"].includes(input.action));
      record.revision += 1;
      if (input.action === "sync") {
        record.state = "syncing";
        runs.set(provider, [
          {
            id: "99999999-9999-4999-8999-999999999999",
            operation: "sync",
            status: "queued",
            attempts: 0,
            created_at: "2026-10-13T02:00:00Z",
            finished_at: null,
            error: null,
          },
        ]);
      } else {
        record.enabled = input.action === "resume";
        record.state = "idle";
        record.next_sync_at = record.enabled ? "2026-10-13T08:00:00Z" : null;
      }
      json(response, output(record));
      return true;
    }
    json(response, {}, 405);
    return true;
  }

  function startRuns() {
    for (const jobs of runs.values()) {
      for (const job of jobs) {
        job.status = "running";
        job.attempts = 1;
      }
    }
  }

  function completeRuns() {
    for (const [provider, jobs] of runs) {
      for (const job of jobs) {
        job.status = "succeeded";
        job.attempts = 1;
        job.finished_at = "2026-10-13T02:01:00Z";
      }
      const record = records.get(provider);
      record.state = "idle";
      record.products = 12;
      record.qualified = 10;
      record.commercial_approved = 10;
      record.checking = 2;
      record.last_sync_at = "2026-10-13T02:01:00Z";
    }
  }
  return {
    handle,
    writes,
    previews,
    logoUploads,
    logoWrites,
    jobReads,
    startRuns,
    completeRuns,
    holdLogoUpload() {
      let release;
      logoUploadGate = new Promise((resolve) => {
        release = resolve;
      });
      return () => {
        logoUploadGate = null;
        release();
      };
    },
    failNextLogoUpload() {
      failLogoUpload = true;
    },
  };
}

async function noOverflowOrDialog(page) {
  assert.equal(await page.getByRole("dialog").count(), 0, "Connection management uses pages");
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1),
    false,
    "Connection pages fit the viewport",
  );
}

async function assertConnectionSelect(page, label) {
  const trigger = page.getByRole("combobox", { name: label, exact: true });
  assert.equal(await trigger.evaluate((node) => node.tagName), "BUTTON");
  assert.match(await trigger.getAttribute("class"), /shared-select-trigger/);
  await assertSelectMenu(page, label);
  const menu = page.locator(".shared-select-popover:visible");
  const layout = await menu.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return {
      top: box.top,
      bottom: box.bottom,
      viewportHeight: innerHeight,
      background: style.backgroundColor,
      border: Number.parseFloat(style.borderTopWidth),
    };
  });
  assert.ok(layout.top >= 0 && layout.bottom <= layout.viewportHeight + 1);
  assert.notEqual(layout.background, "rgba(0, 0, 0, 0)", "Shared menu has a themed surface");
  assert.ok(layout.border > 0, "Shared menu has a visible border");
}

async function assertSelectClosedAndFocused(page, trigger) {
  await page.locator(".shared-select-popover:visible").waitFor({ state: "hidden" });
  const handle = await trigger.elementHandle();
  try {
    await page.waitForFunction((node) => node === document.activeElement, handle);
  } finally {
    await handle.dispose();
  }
  assert.equal(await trigger.getAttribute("aria-expanded"), "false");
}

async function assertConnectorPreview(page, fixture, { exerciseMappings = true } = {}) {
  const json = page.getByRole("textbox", { name: "API response JSON", exact: true });
  await json.waitFor();
  const found = page.getByLabel("Product name found in sample", { exact: true });
  await found.waitFor();
  assert.equal(await found.locator("svg.lucide-check").count(), 1);
  const name = page.getByRole("textbox", { name: "Product name paths", exact: true });
  const original = await name.inputValue();
  const nameLine = json.locator('[data-mappings="fields.name"]');
  assert.match(await name.getAttribute("class"), /border-emerald-600/);
  assert.match(await nameLine.getAttribute("class"), /border-emerald-600/);
  assert.match(await nameLine.innerText(), /"name": "Preview developer tool"/);
  const writes = fixture.writes.length;
  if (exerciseMappings) {
    const requests = fixture.previews.filter((item) => item.kind === "connector-response").length;
    await name.fill("display_name");
    await page.waitForFunction(() => {
      const line = document.querySelector('[data-mappings="fields.name"]');
      return line?.textContent.includes('"display_name"');
    });
    await name.fill("missing.name");
    await page.getByLabel("Product name not found in this sample", { exact: true }).waitFor();
    assert.equal(await json.locator('[data-mappings="fields.name"]').count(), 0);
    assert.notEqual(await name.getAttribute("aria-invalid"), "true");
    assert.equal(
      await page
        .getByRole("form", { name: "Import connection settings" })
        .getByRole("alert")
        .count(),
      0,
    );
    assert.equal(
      await page
        .getByRole("complementary", { name: "API response preview" })
        .getByRole("alert")
        .count(),
      0,
    );
    await name.fill(original);
    await found.waitFor();
    // A mapping-only edit must stay local even after the automatic request debounce.
    await page.waitForTimeout(850);
    assert.equal(
      fixture.previews.filter((item) => item.kind === "connector-response").length,
      requests,
      "Mapping changes recolor the existing sample without fetching the product API again",
    );
  }
  const test = page.getByRole("button", { name: "Test import connection", exact: true });
  await test.click();
  const result = page.getByRole("status", { name: "Import test result", exact: true });
  await result.waitFor();
  assert.match(await result.innerText(), /1 product found/);
  assert.match(await result.innerText(), /Preview developer tool/);
  assert.match(await result.innerText(), /No products were imported and no settings were saved/);
  assert.equal(fixture.writes.length, writes, "Testing the draft does not persist a connection");
  if (exerciseMappings) {
    await name.fill("display_name");
    assert.equal(await result.count(), 0, "Mapping changes clear the previous parsed import test");
    await name.fill(original);
    await test.click();
    await result.waitFor();
  }
  const previewRoute = "**/api/v1/partner/**/connector-preview";
  await page.route(previewRoute, (route) =>
    route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Detail endpoint requires only the {id} placeholder" }),
    }),
  );
  await test.click();
  await page
    .getByText("Detail endpoint requires only the {id} placeholder", { exact: true })
    .waitFor();
  await page.unroute(previewRoute);
  await test.click();
  await result.waitFor();
  assert.equal(fixture.writes.length, writes);
  return { json, result };
}

export async function assertConnectionJourneys({ page, origin, account, fixture, artifactDir }) {
  const base = `${origin}/connections`;
  const logoCounts = () => ({
    url: fixture.logoWrites.length,
    upload: fixture.logoUploads.length,
    settings: fixture.writes.filter((item) => item.method === "PUT" && !item.subresource).length,
  });
  await installConnectionLogoMedia(page.context());
  const firstCredential = "browser-fixture-only-token-one";
  const replacementCredential = "browser-fixture-only-token-two";
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base);
  await page.getByRole("heading", { name: "Import connections", exact: true }).waitFor();
  await page.getByRole("link", { name: "Create connection", exact: true }).click();
  await page.waitForURL(`${base}/new`);
  await page.getByRole("combobox", { name: "Partner account", exact: true }).click();
  await page.getByRole("option", { name: account.name, exact: true }).click();
  await page.getByRole("textbox", { name: /Connection name/ }).fill("Connected product catalog");
  await page.getByRole("textbox", { name: /API base URL/ }).fill("https://catalog.example");
  await page.getByRole("textbox", { name: "Logo URL", exact: true }).fill(logoSource);
  const scheduled = page.getByRole("checkbox", { name: "Enable scheduled imports", exact: true });
  assert.equal(await scheduled.evaluate((node) => node.tagName), "BUTTON");
  assert.match(await scheduled.getAttribute("class"), /shared-checkbox/);
  assert.equal(await scheduled.isChecked(), true);
  await scheduled.focus();
  await page.keyboard.press("Space");
  assert.equal(await scheduled.isChecked(), false);
  await page.keyboard.press("Space");
  assert.equal(await scheduled.isChecked(), true);

  const authentication = page.getByRole("combobox", { name: "Authentication method", exact: true });
  await authentication.focus();
  await page.keyboard.press("ArrowDown");
  await assertConnectionSelect(page, "Authentication method");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await assertSelectClosedAndFocused(page, authentication);
  assert.equal(await authentication.innerText(), "Bearer token");
  await authentication.click();
  await page.getByRole("option", { name: "API key", exact: true }).click();
  await page.getByRole("textbox", { name: "API key header", exact: true }).waitFor();
  assert.equal(await page.getByLabel(/^Bearer token/).count(), 0);
  await authentication.click();
  await page.getByRole("option", { name: "Bearer token", exact: true }).click();
  assert.equal(await page.getByRole("textbox", { name: "API key header", exact: true }).count(), 0);
  await page.getByLabel(/^Bearer token/).fill(firstCredential);

  const pagination = page.getByRole("combobox", { name: "Pagination mode", exact: true });
  const parameter = page.getByRole("textbox", { name: "Pagination parameter", exact: true });
  const nextPath = page.getByRole("textbox", { name: "Next token path", exact: true });
  const pageSize = page.getByRole("spinbutton", { name: "Page size", exact: true });
  assert.equal(await pagination.innerText(), "None");
  assert.equal(await parameter.count(), 0);
  assert.equal(await nextPath.count(), 0);
  assert.equal(await pageSize.count(), 1);
  await pagination.focus();
  await page.keyboard.press("Enter");
  await assertConnectionSelect(page, "Pagination mode");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await assertSelectClosedAndFocused(page, pagination);
  assert.equal(await pagination.innerText(), "None", "Escape preserves the saved choice");
  await page.keyboard.press("Enter");
  await page.keyboard.press("c");
  await page.keyboard.press("Enter");
  await assertSelectClosedAndFocused(page, pagination);
  assert.equal(
    await pagination.innerText(),
    "Cursor",
    "Keyboard typeahead chooses a pagination mode",
  );
  assert.equal(await parameter.inputValue(), "cursor");
  assert.equal(await nextPath.inputValue(), "nextCursor");
  assert.equal(await pageSize.inputValue(), "10");
  for (const [mode, hasParameter, hasNextPath] of [
    ["Next-page URL", false, true],
    ["Page number", true, false],
    ["Offset", true, false],
    ["None", false, false],
  ]) {
    await pagination.click();
    await page.getByRole("option", { name: mode, exact: true }).click();
    assert.equal(await parameter.count(), Number(hasParameter), `${mode} pagination parameter`);
    assert.equal(await nextPath.count(), Number(hasNextPath), `${mode} next-result path`);
    assert.equal(await pageSize.count(), 1);
  }
  await pagination.click();
  await page.getByRole("option", { name: "Cursor", exact: true }).click();
  await parameter.fill("after");
  await nextPath.fill("meta.next_cursor");
  await page.getByRole("textbox", { name: /Page size parameter/ }).fill("limit");
  await pageSize.fill("25");
  await pagination.click();
  await assertConnectionSelect(page, "Pagination mode");
  await page.screenshot({
    path: join(artifactDir, "partner-connection-pagination-desktop.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await assertSelectClosedAndFocused(page, pagination);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const label of ["Authentication method", "Pagination mode"]) {
    const trigger = page.getByRole("combobox", { name: label, exact: true });
    await trigger.click();
    await assertConnectionSelect(page, label);
    if (label === "Pagination mode") {
      await page.screenshot({
        path: join(artifactDir, "partner-connection-pagination-mobile.png"),
        fullPage: true,
      });
    }
    await page.keyboard.press("Escape");
    await assertSelectClosedAndFocused(page, trigger);
  }
  await noOverflowOrDialog(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await noOverflowOrDialog(page);
  await assertConnectorPreview(page, fixture);
  await assertConnectorFilterColors(page, {
    responseCount: () =>
      fixture.previews.filter((item) => item.kind === "connector-response").length,
    screenshotPrefix: join(artifactDir, "connection-filter-colors"),
  });
  await page.screenshot({
    path: join(artifactDir, "partner-connection-create-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("combobox", { name: "Sync interval", exact: true }).click();
  assert.deepEqual(await page.getByRole("option").allTextContents(), ["12 hours", "24 hours"]);
  await page.screenshot({
    path: join(artifactDir, "partner-sync-interval-options.png"),
    fullPage: true,
  });
  await page.getByRole("option", { name: "12 hours", exact: true }).click();
  await page.getByRole("button", { name: "Create connection", exact: true }).click();
  await page.waitForURL(/\/connections\/partner-browser-connection-1$/);
  const created = fixture.writes.find((item) => item.method === "POST" && !item.subresource);
  assert.equal(created.input.logo_url, logoSource);
  assert.equal(created.input.enabled, true);
  assert.equal(created.input.sync_interval_minutes, 720);
  assert.equal(created.input.connector.auth.mode, "bearer");
  assert.equal(created.input.connector.pagination.mode, "cursor");
  assert.equal(created.input.connector.pagination.parameter, "after");
  assert.equal(created.input.connector.pagination.next_path, "meta.next_cursor");
  assert.equal(created.input.connector.pagination.size_parameter, "limit");
  assert.equal(created.input.connector.pagination.page_size, 25);
  const detail = page.url();
  await page.getByRole("heading", { name: "Connected product catalog", exact: true }).waitFor();
  await assertManagedConnectionLogo(page.getByRole("main"), managedLogo);
  await page.getByText("Bearer token configured", { exact: true }).waitFor();
  assert.equal((await page.locator("body").innerText()).includes(firstCredential), false);
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await page.getByRole("button", { name: "Sync now", exact: true }).click();
  const syncRun = page.getByRole("row").filter({
    has: page.getByRole("cell", { name: "Catalog sync", exact: true }),
  });
  await syncRun.getByRole("cell", { name: "queued", exact: true }).waitFor();
  await syncRun.getByRole("cell", { name: "Queued", exact: true }).waitFor();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const readsWhileHidden = fixture.jobReads.length;
  fixture.startRuns();
  await page.waitForTimeout(3500);
  assert.equal(
    fixture.jobReads.length,
    readsWhileHidden,
    "Import polling pauses while the browser visibility event reports a hidden page",
  );
  await page.evaluate(() => {
    delete document.visibilityState;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await syncRun.getByRole("cell", { name: "running", exact: true }).waitFor();
  await syncRun.getByRole("cell", { name: "In progress", exact: true }).waitFor();
  await page.screenshot({
    path: join(artifactDir, "partner-connection-running-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.waitForURL(`${detail}/edit`);
  await page.getByRole("textbox", { name: /Connection name/ }).waitFor();
  const readsAfterNavigation = fixture.jobReads.length;
  await page.waitForTimeout(3500);
  assert.equal(
    fixture.jobReads.length,
    readsAfterNavigation,
    "Leaving the connection details stops polling its import runs",
  );
  await page.goto(detail);
  await syncRun.getByRole("cell", { name: "running", exact: true }).waitFor();
  fixture.completeRuns();
  await syncRun.getByRole("cell", { name: "succeeded", exact: true }).waitFor();
  await syncRun.locator('time[datetime="2026-10-13T02:01:00Z"]').waitFor();
  await page.getByText("Ready", { exact: true }).waitFor();
  assert.equal(
    await page.getByRole("link", { name: "12", exact: true }).count(),
    1,
    "Finishing a run automatically refreshes the connection's product totals",
  );
  const readsAfterCompletion = fixture.jobReads.length;
  await page.waitForTimeout(3500);
  assert.equal(
    fixture.jobReads.length,
    readsAfterCompletion,
    "Completed imports stop automatic polling",
  );
  console.log(
    "Connection imports update queued/running/finished status and product totals automatically; polling stops after completion/navigation and pauses on simulated hidden-page visibility.",
  );
  await page.getByRole("button", { name: "Pause imports", exact: true }).click();
  await page.getByRole("button", { name: "Resume imports", exact: true }).waitFor();
  await page.getByRole("button", { name: "Resume imports", exact: true }).click();
  await page.getByRole("button", { name: "Pause imports", exact: true }).waitFor();
  await noOverflowOrDialog(page);
  await page.screenshot({
    path: join(artifactDir, "partner-connection-detail-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox", { name: /Connection name/ }).fill("Renamed product catalog");
  assert.equal(
    await page.getByRole("textbox", { name: "Logo URL", exact: true }).inputValue(),
    managedLogo,
  );
  await assertManagedConnectionLogo(
    page.getByRole("form", { name: "Import connection settings" }),
    managedLogo,
  );
  await page.getByRole("textbox", { name: "API response JSON", exact: true }).waitFor();
  await assertConnectionLogoEditor(page, artifactDir, "partner");
  const logoUrl = page.getByRole("textbox", { name: "Logo URL", exact: true });
  await logoUrl.fill(logoSource);
  await logoUrl.blur();
  await page.getByRole("status").filter({ hasText: "Logo updated." }).waitFor();
  assert.equal(
    await logoUrl.inputValue(),
    managedLogo,
    "URL imports replace the input with the canonical R2 URL",
  );
  assert.equal(fixture.logoWrites.at(-1).logo_url, logoSource);
  await assertInvalidConnectionLogo(
    page,
    () => fixture.writes.length + fixture.logoUploads.length + fixture.logoWrites.length,
  );
  const beforeUploadClick = logoCounts();
  const releaseLogo = fixture.holdLogoUpload();
  try {
    await selectConnectionLogo(page, { immediate: true });
    await page.screenshot({
      path: join(artifactDir, "partner-connection-logo-local-preview.png"),
      fullPage: true,
    });
  } finally {
    releaseLogo();
  }
  await page.getByRole("status").filter({ hasText: "Logo updated." }).waitFor();
  assert.equal(await logoUrl.inputValue(), uploadedLogo);
  assert.deepEqual(
    logoCounts(),
    { ...beforeUploadClick, upload: beforeUploadClick.upload + 1 },
    "The first Upload click from an edited URL sends only the selected file",
  );
  assert.equal(
    await page.getByRole("textbox", { name: /Connection name/ }).inputValue(),
    "Renamed product catalog",
    "Immediate logo updates preserve the unsaved connection name",
  );
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  const { json: editJson } = await assertConnectorPreview(page, fixture, {
    exerciseMappings: false,
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForFunction(() => window.scrollY === 0);
  const form = page.getByRole("form", { name: "Import connection settings", exact: true });
  const previewPanel = page.getByRole("complementary", {
    name: "API response preview",
    exact: true,
  });
  const formBox = await form.boundingBox();
  const previewBox = await previewPanel.boundingBox();
  assert.ok(previewBox.x >= formBox.x + formBox.width, "Desktop preview sits beside the editor");
  assert.ok(Math.abs(previewBox.y - formBox.y) < 2, "Desktop editor and preview align at the top");
  assert.equal(
    Object.hasOwn(fixture.previews.filter((item) => item.provider).at(-1).input, "credential"),
    false,
    "A stored write-only credential is reused without returning it to the portal",
  );
  await page.screenshot({
    path: join(artifactDir, "partner-connection-edit-preview-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflowOrDialog(page);
  await editJson.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: join(artifactDir, "partner-connection-edit-preview-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await saveConnectionFromEditedLogoUrl(page, detail, logoCounts);
  await page.getByRole("heading", { name: "Renamed product catalog", exact: true }).waitFor();
  await assertManagedConnectionLogo(page.getByRole("main"), managedLogo);
  assert.equal(fixture.logoUploads.length, 1);
  let write = fixture.writes.filter((item) => item.method === "PUT").at(-1);
  assert.equal(write.input.logo_url, logoSource, "Same-form Save consumes the unsaved URL draft");
  assert.equal(
    Object.hasOwn(write.input, "credential"),
    false,
    "Unchanged write-only credentials stay omitted",
  );
  assert.equal(
    Object.hasOwn(write.input, "connector"),
    false,
    "Renaming does not rewrite connector settings",
  );
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  const replaceCredential = page.getByRole("checkbox", { name: "Replace credential", exact: true });
  assert.equal(await replaceCredential.evaluate((node) => node.tagName), "BUTTON");
  assert.match(await replaceCredential.getAttribute("class"), /shared-checkbox/);
  await replaceCredential.focus();
  await page.keyboard.press("Space");
  assert.equal(await replaceCredential.isChecked(), true);
  assert.equal(await page.getByLabel(/^Bearer token/).inputValue(), "");
  await page.getByLabel(/^Bearer token/).fill(replacementCredential);
  const apiBase = page.getByRole("textbox", { name: "API base URL", exact: true });
  const responseJson = page.getByRole("textbox", { name: "API response JSON", exact: true });
  await responseJson.waitFor();
  await apiBase.fill("https://other-catalog.example");
  await removeConnectionLogoFromEditedUrl(page, logoCounts);
  assert.equal(fixture.logoWrites.at(-1).logo_url, null);
  assert.equal(
    await apiBase.inputValue(),
    "https://other-catalog.example",
    "Immediate logo removal preserves the unsaved API origin",
  );
  assert.equal(await page.getByLabel(/^Bearer token/).inputValue(), "");
  assert.equal(
    await responseJson.count(),
    0,
    "An origin change clears the previous response sample",
  );
  await page.getByText(/Enter a credential for this API and authentication method/).waitFor();
  await apiBase.fill("https://catalog.example");
  await page.getByLabel(/^Bearer token/).fill(replacementCredential);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(detail);
  await page.getByText("Bearer token configured", { exact: true }).waitFor();
  write = fixture.writes.filter((item) => item.method === "PUT").at(-1);
  assert.equal(write.input.credential, replacementCredential);
  assert.equal(
    Object.hasOwn(write.input, "logo_url"),
    false,
    "Settings saves do not reimport the independently saved logo",
  );
  assert.equal(await page.getByRole("main").locator(`img[src="${uploadedLogo}"]`).count(), 0);
  assert.equal((await page.locator("body").innerText()).includes(replacementCredential), false);
  await page.getByRole("link", { name: "Edit", exact: true }).click();
  await page.getByRole("combobox", { name: "Authentication method", exact: true }).click();
  await page.getByRole("option", { name: "No authentication", exact: true }).click();
  await page
    .getByText("Saving without authentication removes the stored credential.", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(detail);
  await page.getByText("No credential configured", { exact: true }).waitFor();
  await page.goto(base);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("link", { name: "Renamed product catalog", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: /^Filter connections by/ }).count(), 0);
  assert.equal(
    await page.getByRole("link", { name: "Clear account filter", exact: true }).count(),
    0,
  );
  assert.equal(
    await page.getByRole("combobox", { name: "Partner account", exact: true }).count(),
    0,
  );
  await page.getByRole("columnheader", { name: "Account", exact: true }).waitFor();
  await page.getByRole("link", { name: account.name, exact: true }).waitFor();
  await noOverflowOrDialog(page);
  await page.screenshot({
    path: join(artifactDir, "partner-connections-mobile.png"),
    fullPage: true,
  });
  await page.goto(detail);
  await page.getByRole("link", { name: "Disconnect", exact: true }).click();
  await page.waitForURL(`${detail}/disconnect`);
  await page
    .getByText(/Your imported products and historical performance remain available/)
    .waitFor();
  await noOverflowOrDialog(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflowOrDialog(page);
  await page.screenshot({
    path: join(artifactDir, "partner-connection-disconnect-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Disconnect connection", exact: true }).click();
  await page.waitForURL(base);
  await page.getByRole("heading", { name: "No import connections", exact: true }).waitFor();
  assert.equal(
    await page.getByRole("link", { name: "Renamed product catalog", exact: true }).count(),
    0,
  );
  await noOverflowOrDialog(page);
  await page.screenshot({
    path: join(artifactDir, "partner-connections-empty-mobile.png"),
    fullPage: true,
  });
  assert.equal(fixture.writes.filter((item) => item.method === "DELETE").length, 1);
  assert.deepEqual(
    fixture.writes
      .filter((item) => item.subresource === "actions")
      .map((item) => item.input.action),
    ["sync", "pause", "resume"],
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/new`);
  await page.getByRole("combobox", { name: "Partner account", exact: true }).click();
  await page.getByRole("option", { name: account.name, exact: true }).click();
  await page.getByRole("textbox", { name: /Connection name/ }).fill("Retry-safe logo catalog");
  await page.getByRole("textbox", { name: /API base URL/ }).fill("https://catalog.example");
  await selectConnectionLogo(page);
  fixture.failNextLogoUpload();
  const createsBefore = fixture.writes.filter(
    (item) => item.method === "POST" && !item.subresource,
  ).length;
  await page.getByRole("button", { name: "Create connection", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Connection settings saved, but the logo was not uploaded." })
    .waitFor();
  assert.equal(
    await page.getByRole("textbox", { name: /Connection name/ }).inputValue(),
    "Retry-safe logo catalog",
  );
  assert.equal(
    await page.getByRole("textbox", { name: /API base URL/ }).inputValue(),
    "https://catalog.example",
  );
  await page
    .getByRole("img", { name: "Platform logo preview", exact: true })
    .locator('img[src^="blob:"]')
    .waitFor();
  await noOverflowOrDialog(page);
  await page.screenshot({
    path: join(artifactDir, "partner-connection-logo-retry-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(/\/connections\/partner-browser-connection-2$/);
  assert.equal(
    fixture.writes.filter((item) => item.method === "POST" && !item.subresource).length,
    createsBefore + 1,
    "Retry after a failed logo upload updates the saved connection without creating a duplicate",
  );
  assert.equal(fixture.logoUploads.at(-1).provider, "partner-browser-connection-2");
  assert.equal(fixture.logoUploads.at(-1).expected_revision, 2);
  await assertManagedConnectionLogo(page.getByRole("main"), uploadedLogo);
  await page.goto(base);
  const retryLink = page.getByRole("link", { name: "Retry-safe logo catalog", exact: true });
  await retryLink.waitFor();
  await assertManagedConnectionLogo(page.getByRole("row").filter({ has: retryLink }), uploadedLogo);
  await page.screenshot({
    path: join(artifactDir, "partner-connection-logo-list-mobile.png"),
    fullPage: true,
  });
}

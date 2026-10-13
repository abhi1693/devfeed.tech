import assert from "node:assert/strict";
import { join } from "node:path";
import { assertSelectMenu } from "../../../scripts/testing/select-menus.mjs";

export function createConnectionFixture({ account, origin }) {
  assert.match(account.id, /^[a-f0-9-]{36}$/i);
  const records = new Map();
  const archived = new Set();
  const credentials = new Map();
  const runs = new Map();
  const writes = [];
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
    const scoped =
      /^\/v1\/partner\/accounts\/([^/]+)\/connections(?:\/([^/]+))?(?:\/(actions|jobs))?$/.exec(
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
      json(response, { items, total: items.length, limit: 10, offset: 0 });
      return true;
    }
    if (provider && request.method === "GET") {
      json(response, output(record));
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
      assert.equal(connector.auth?.source, "connection");
      assert.equal(connector.auth?.secret_ref, "");
      const created = {
        provider: id,
        account_id: owner.id,
        account_name: owner.name,
        credentials_available: true,
        name: input.name,
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
        credentials_managed: false,
        connector_editable: true,
      };
      records.set(id, created);
      if (input.credential) credentials.set(id, input.credential);
      json(response, output(created), 201);
      return true;
    }
    if (request.method === "PUT" && !subresource) {
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
  return { handle, writes, completeRuns };
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

export async function assertConnectionJourneys({ page, origin, account, fixture, artifactDir }) {
  const base = `${origin}/connections`;
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
  const nextPath = page.getByRole("textbox", { name: "Next cursor or URL path", exact: true });
  const pageSize = page.getByRole("spinbutton", { name: "Page size", exact: true });
  assert.equal(await pagination.innerText(), "Single response");
  assert.equal(await parameter.count(), 0);
  assert.equal(await nextPath.count(), 0);
  assert.equal(await pageSize.count(), 0);
  await pagination.focus();
  await page.keyboard.press("Enter");
  await assertConnectionSelect(page, "Pagination mode");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await assertSelectClosedAndFocused(page, pagination);
  assert.equal(
    await pagination.innerText(),
    "Single response",
    "Escape preserves the saved choice",
  );
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
    ["Single response", false, false],
  ]) {
    await pagination.click();
    await page.getByRole("option", { name: mode, exact: true }).click();
    assert.equal(await parameter.count(), Number(hasParameter), `${mode} pagination parameter`);
    assert.equal(await nextPath.count(), Number(hasNextPath), `${mode} next-result path`);
    assert.equal(await pageSize.count(), mode === "Single response" ? 0 : 1);
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
  await page.screenshot({
    path: join(artifactDir, "partner-connection-create-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Create connection", exact: true }).click();
  await page.waitForURL(/\/connections\/partner-browser-connection-1$/);
  const created = fixture.writes.find((item) => item.method === "POST" && !item.subresource);
  assert.equal(created.input.enabled, true);
  assert.equal(created.input.connector.auth.mode, "bearer");
  assert.equal(created.input.connector.pagination.mode, "cursor");
  assert.equal(created.input.connector.pagination.parameter, "after");
  assert.equal(created.input.connector.pagination.next_path, "meta.next_cursor");
  assert.equal(created.input.connector.pagination.size_parameter, "limit");
  assert.equal(created.input.connector.pagination.page_size, 25);
  const detail = page.url();
  await page.getByRole("heading", { name: "Connected product catalog", exact: true }).waitFor();
  await page.getByText("Bearer token configured", { exact: true }).waitFor();
  assert.equal((await page.locator("body").innerText()).includes(firstCredential), false);
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await page.getByRole("button", { name: "Sync now", exact: true }).click();
  await page.getByText("queued", { exact: true }).waitFor();
  fixture.completeRuns();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByText("succeeded", { exact: true }).waitFor();
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
  assert.equal(await page.locator('input[type="password"]').count(), 0);
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(detail);
  await page.getByRole("heading", { name: "Renamed product catalog", exact: true }).waitFor();
  let write = fixture.writes.filter((item) => item.method === "PUT").at(-1);
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
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await page.waitForURL(detail);
  await page.getByText("Bearer token configured", { exact: true }).waitFor();
  write = fixture.writes.filter((item) => item.method === "PUT").at(-1);
  assert.equal(write.input.credential, replacementCredential);
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
}

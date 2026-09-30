import assert from "node:assert/strict";

export const testMcpEndpoint = "https://mcp.ingress.test/devfeed/mcp";

async function assertCodeWraps(block) {
  assert.equal(
    await block.evaluate((node) => node.scrollWidth <= node.clientWidth),
    true,
    "Setup text wraps without horizontal scrolling",
  );
}

export async function checkMcp(page, screenshotPrefix) {
  const original = page.url();
  const viewport = page.viewportSize();
  const theme = await page.evaluate(() => document.documentElement.classList.contains("dark"));
  const link = page
    .locator(".sidebar")
    .getByRole("link", { name: "Connect your agent", exact: true });
  await link.click();
  await page.getByRole("heading", { name: "Connect your agent", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Server documentation" }).count(), 0);
  assert.equal(await page.locator(".mcp-page-header p").count(), 0);
  assert.equal(await link.getAttribute("aria-current"), "page");
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  assert.equal(await link.locator("span").textContent(), "Connect your agent");
  const human = page.getByRole("tab", { name: "I'm a Human", exact: true });
  const agent = page.getByRole("tab", { name: "I'm an Agent", exact: true });
  assert.equal(await human.getAttribute("aria-selected"), "true");
  await page.waitForFunction(
    (value) => document.querySelector("#mcp-endpoint")?.value === value,
    testMcpEndpoint,
  );
  const config = page.getByLabel("MCP configuration", { exact: true });
  assert.equal(JSON.parse(await config.textContent()).servers.devfeed.type, "http");
  assert.equal(JSON.parse(await config.textContent()).servers.devfeed.url, testMcpEndpoint);
  assert.equal(await page.getByRole("radio").count(), 5);
  assert.equal(await page.locator(".mcp-client-check").count(), 0);
  await assertCodeWraps(config);
  assert.equal(await page.getByRole("button", { name: "My account", exact: true }).count(), 0);
  assert.equal(
    await page.getByRole("button", { name: "Public discovery", exact: true }).count(),
    0,
  );
  assert.equal(await page.getByText("Choose your tool", { exact: true }).count(), 0);
  assert.equal(
    await page.getByText("Configure the connection manually.", { exact: true }).count(),
    0,
  );
  assert.equal(await page.locator(".mcp-client-option small").count(), 0);
  assert.equal(await page.locator(".mcp-clients img").count(), 3);
  assert.equal(
    await page
      .locator(".mcp-clients img")
      .evaluateAll((nodes) => nodes.every((node) => node.complete && node.naturalWidth > 0)),
    true,
  );
  const server = page.getByRole("region", { name: "Server connection", exact: true });
  assert.equal(await server.locator("p").count(), 0);
  assert.equal(await page.getByText("Sign in to DevFeed", { exact: true }).count(), 1);
  const signIn = page.locator(".mcp-sign-in");
  for (const name of ["VS Code", "Codex", "Claude Code", "Cursor", "Other"]) {
    await page.getByRole("radio", { name: new RegExp(`^${name}`) }).check();
    await signIn.locator("summary").click();
    assert.equal(await signIn.locator("ol > li").count(), 3);
    assert.equal(
      await signIn.getByText(/review permissions, and select Allow connection/).isVisible(),
      true,
    );
    if (name === "Codex") {
      assert.equal(
        await page.getByLabel("Sign-in command", { exact: true }).textContent(),
        "codex mcp login devfeed",
      );
      await page.evaluate(() => {
        window.__mcpSignInCopied = null;
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: async (text) => {
              window.__mcpSignInCopied = text;
            },
          },
        });
      });
      await page.getByRole("button", { name: "Copy sign-in command", exact: true }).click();
      assert.equal(await page.evaluate(() => window.__mcpSignInCopied), "codex mcp login devfeed");
      await page.screenshot({
        path: `${screenshotPrefix}-sign-in.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
    await signIn.locator("summary").click();
  }
  await page.getByRole("radio", { name: /^Cursor/ }).check();
  await page.getByLabel("MCP server URL", { exact: true }).fill("https://agents.example.com/mcp");
  assert.equal(
    JSON.parse(await config.textContent()).mcpServers.devfeed.url,
    "https://agents.example.com/mcp",
  );
  await page.evaluate(() => {
    window.__mcpCopied = null;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text) => {
          window.__mcpCopied = text;
        },
      },
    });
  });
  await page.getByRole("button", { name: "Copy configuration" }).click();
  assert.equal(
    JSON.parse(await page.evaluate(() => window.__mcpCopied)).mcpServers.devfeed.url,
    "https://agents.example.com/mcp",
  );
  for (const [name, command] of [
    ["Codex", "codex mcp add devfeed --url"],
    ["Claude Code", "claude mcp add --transport http --scope local devfeed"],
  ]) {
    await page.getByRole("radio", { name: new RegExp(`^${name}`) }).check();
    assert.equal((await config.textContent()).startsWith(command), true);
    await assertCodeWraps(config);
    await page.getByRole("button", { name: "Copy command" }).click();
    assert.equal((await page.evaluate(() => window.__mcpCopied)).startsWith(command), true);
  }
  await human.focus();
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(
    () =>
      document.querySelector('[role="tab"][data-state="active"]')?.textContent === "I'm an Agent",
  );
  assert.equal(await agent.evaluate((node) => node === document.activeElement), true);
  const prompt = page.getByLabel("Agent setup prompt", { exact: true });
  await assertCodeWraps(prompt);
  assert.equal(
    (await prompt.textContent()).includes("Set up the DevFeed MCP server in Claude Code."),
    true,
  );
  await page.getByRole("radio", { name: /^Codex/ }).check();
  assert.equal((await prompt.textContent()).includes("codex mcp add devfeed"), true);
  await page.getByRole("button", { name: "Copy setup prompt" }).click();
  const copiedPrompt = await page.evaluate(() => window.__mcpCopied);
  assert.equal(
    copiedPrompt.split("\n").find((line) => line.startsWith("Server URL: ")),
    "Server URL: https://agents.example.com/mcp",
  );
  assert.equal(copiedPrompt.includes("Preserve other servers and settings."), true);
  assert.equal(copiedPrompt.includes("Only report success after a real tool call"), true);
  await page.evaluate(() => {
    window.__mcpFallback = null;
    navigator.clipboard.writeText = async () => {
      throw new Error("Clipboard denied");
    };
    document.addEventListener(
      "copy",
      () => {
        window.__mcpFallback = document.activeElement?.value;
      },
      { once: true },
    );
  });
  await page.getByRole("button", { name: "Copy setup prompt" }).click();
  await page.waitForFunction(() => window.__mcpFallback !== null);
  assert.equal(await page.evaluate(() => window.__mcpFallback), copiedPrompt);
  assert.equal(
    await page.getByText("Couldn’t copy. Select the text and copy it manually.").count(),
    0,
  );
  await page.evaluate(() => {
    navigator.clipboard.writeText = async (text) => {
      window.__mcpCopied = text;
    };
  });
  await page.getByLabel("MCP server URL", { exact: true }).fill("invalid");
  assert.equal(await page.getByRole("button", { name: "Copy setup prompt" }).count(), 0);
  await page.getByLabel("MCP server URL", { exact: true }).fill(testMcpEndpoint);
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: `${screenshotPrefix}-agent.png`,
    fullPage: true,
    animations: "disabled",
  });
  await agent.focus();
  await page.keyboard.press("ArrowLeft");
  await config.waitFor();
  await page.getByRole("radio", { name: /^Other/ }).check();
  await page.getByRole("button", { name: "Copy URL", exact: true }).click();
  assert.equal(await page.evaluate(() => window.__mcpCopied), testMcpEndpoint);
  await page.getByRole("radio", { name: /^VS Code/ }).check();
  const reference = page.getByRole("region", { name: "Available tools", exact: true });
  assert.equal(await reference.evaluate((node) => node.tagName), "SECTION");
  await page.getByRole("table", { name: "DevFeed MCP tools" }).waitFor();
  assert.equal(await page.getByRole("table").getByRole("row").count(), 15);
  await page.setViewportSize({ width: 2048, height: 1100 });
  const workspaceBounds = await page.locator(".mcp-workspace").boundingBox();
  const referenceBounds = await reference.boundingBox();
  assert.equal(Math.abs(workspaceBounds.y - referenceBounds.y) < 1, true);
  assert.equal(referenceBounds.x >= workspaceBounds.x + workspaceBounds.width, true);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    "Setup and tools fit side by side on wide screens",
  );
  for (const mode of ["light", "dark"]) {
    await page.evaluate(
      (value) => document.documentElement.classList.toggle("dark", value === "dark"),
      mode,
    );
    await page.screenshot({
      path: `${screenshotPrefix}-wide-${mode}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  const stackedWorkspace = await page.locator(".mcp-workspace").boundingBox();
  const stackedReference = await reference.boundingBox();
  assert.equal(stackedReference.y >= stackedWorkspace.y + stackedWorkspace.height, true);
  await page.setViewportSize(viewport);
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  for (const mode of ["light", "dark"]) {
    await page.evaluate(
      (value) => document.documentElement.classList.toggle("dark", value === "dark"),
      mode,
    );
    await page.screenshot({
      path: `${screenshotPrefix}-${mode}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  await page.setViewportSize({ width: 360, height: 800 });
  const mobile = page.locator(".mobile-nav").getByRole("link", { name: "Connect your agent" });
  assert.equal(await mobile.isVisible(), true);
  assert.equal(await mobile.getAttribute("aria-current"), "page");
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  const manualFits = await page.locator(".mcp-manual-grid").evaluate((grid) => {
    const right = grid.getBoundingClientRect().right;
    const code = grid.querySelector(".mcp-code-block");
    return (
      code.getBoundingClientRect().right <= right + 1 &&
      [...grid.querySelectorAll(".mcp-steps p")].every(
        (node) => node.scrollWidth <= node.clientWidth,
      )
    );
  });
  assert.equal(manualFits, true, "Manual steps and configuration stay inside the mobile panel");
  const longEndpoint = `https://agents.example.com/${"a".repeat(180)}/mcp`;
  await page.getByLabel("MCP server URL", { exact: true }).fill(longEndpoint);
  for (const name of ["VS Code", "Codex", "Claude Code", "Cursor", "Other"]) {
    await page.getByRole("radio", { name: new RegExp(`^${name}`) }).check();
    await assertCodeWraps(config);
    await signIn.locator("summary").click();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      true,
    );
    if (name === "Codex") {
      await assertCodeWraps(page.getByLabel("Sign-in command", { exact: true }));
      await page.screenshot({
        path: `${screenshotPrefix}-mobile-sign-in.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
    await signIn.locator("summary").click();
  }
  await page.getByLabel("MCP server URL", { exact: true }).fill(testMcpEndpoint);
  await page.getByRole("radio", { name: /^VS Code/ }).check();
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: `${screenshotPrefix}-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  const mobileWorkspace = await page.locator(".mcp-workspace").boundingBox();
  const mobileReference = await reference.boundingBox();
  assert.equal(mobileReference.y >= mobileWorkspace.y + mobileWorkspace.height, true);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    "The tool reference fits the mobile viewport",
  );
  await agent.click();
  await page.getByRole("radio", { name: /^Claude Code/ }).check();
  await assertCodeWraps(prompt);
  await page.getByRole("button", { name: "Copy setup prompt" }).click();
  assert.equal(
    (await page.evaluate(() => window.__mcpCopied)).includes("claude mcp add --transport http"),
    true,
  );
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: `${screenshotPrefix}-mobile-agent.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.reload();
  await human.waitFor();
  assert.equal(await human.getAttribute("aria-selected"), "true");
  await page.locator(".mobile-nav").getByRole("link", { name: "Latest", exact: true }).click();
  await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
  await mobile.click();
  await human.waitFor();
  await checkMcpConsent(page, screenshotPrefix);
  await page.setViewportSize(viewport);
  await page.goto(original);
  await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
  await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), theme);
}

async function checkMcpConsent(page, screenshotPrefix) {
  const origin = page.url().split("#")[0];
  const extension = origin.startsWith("chrome-extension:");
  const requestId = "r".repeat(43);
  const returnTo = `/mcp/authorize?request=${requestId}`;
  const consentUrl = extension ? `${origin}#${returnTo}` : new URL(returnTo, origin).href;
  let signedIn = false;
  let readOnly = false;
  let approvedAccess = "read-write";
  const identity = async (route) =>
    route.fulfill({
      json: signedIn
        ? {
            user_id: "mcp-reader",
            name: "Review reader",
            email: null,
            csrf_token: "review-csrf",
            expires_at: Date.now() / 1000 + 3600,
          }
        : null,
    });
  const consent = async (route) => {
    if (route.request().method() === "POST") {
      assert.equal(route.request().headers()["x-csrf-token"], "review-csrf");
      assert.deepEqual(route.request().postDataJSON(), { approved: true, access: approvedAccess });
      return route.fulfill({ json: { redirect_url: "https://client.callback.test/done" } });
    }
    return route.fulfill({
      json: {
        client_name: "Review client",
        scopes: readOnly ? ["devfeed:read"] : ["devfeed:read", "devfeed:write"],
        resource: testMcpEndpoint,
        redirect_uri: "https://client.callback.test/done",
      },
    });
  };
  let connected = true;
  const connections = async (route) => {
    if (route.request().method() === "DELETE") {
      assert.equal(route.request().headers()["x-csrf-token"], "review-csrf");
      connected = false;
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({
      json: {
        items: connected
          ? [
              {
                id: "review-grant",
                client_name: "Review client",
                scopes: ["devfeed:read", "devfeed:write"],
                expires_at: Date.now() / 1000 + 86400,
              },
            ]
          : [],
      },
    });
  };
  const callback = async (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Authorization returned to client.</p>" });
  await page.route("**/api/v1/user/auth/me", identity);
  await page.route(`**/api/v1/user/mcp/requests/${requestId}`, consent);
  await page.route("https://client.callback.test/done", callback);
  await page.route("**/api/v1/user/mcp/connections**", connections);
  await page.goto(consentUrl);
  if (extension) await page.reload();
  await page.getByRole("heading", { name: "Sign in to connect your agent", exact: true }).waitFor();
  const popup = extension ? page.context().waitForEvent("page") : null;
  await page.locator(".empty-state").getByRole("link", { name: "Sign in", exact: true }).click();
  const loginPage = popup ? await popup : page;
  await loginPage.waitForURL((url) => url.pathname === "/login");
  const loginResponse = loginPage.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/user/auth/login",
  );
  await loginPage.getByRole("link", { name: "Continue to sign in", exact: true }).click();
  const response = await loginResponse;
  assert.equal(response.status(), 302, "MCP consent return destination is accepted by sign-in");
  assert.equal(
    new URL(response.url()).searchParams.get("return_to"),
    extension ? "/extension/login-complete" : returnTo,
  );
  await loginPage.getByText("Sign-in provider", { exact: true }).waitFor();
  if (extension) await loginPage.close();
  signedIn = true;
  await page.goto(consentUrl);
  if (extension) await page.reload();
  await page.getByRole("heading", { name: "Review client", exact: true }).waitFor();
  const readChoice = page.getByRole("radio", { name: "Read-only", exact: true });
  const writeChoice = page.getByRole("radio", { name: "Read-write", exact: true });
  assert.equal(await writeChoice.isChecked(), true);
  await readChoice.check();
  assert.equal(
    await page.getByRole("heading", { name: "Update your account", exact: true }).count(),
    0,
  );
  await page.getByText(/this agent cannot change/).waitFor();
  await page.screenshot({
    path: `${screenshotPrefix}-consent-read-only.png`,
    fullPage: true,
    animations: "disabled",
  });
  await readChoice.focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(await writeChoice.isChecked(), true);
  assert.equal(await page.getByText(/Save or remove bookmarks/).count(), 1);
  assert.equal(await page.getByText("Read and write", { exact: true }).count(), 1);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.screenshot({
    path: `${screenshotPrefix}-consent.png`,
    fullPage: true,
    animations: "disabled",
  });
  const consentViewport = page.viewportSize();
  const consentTheme = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const [size, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 360, 800],
  ]) {
    await page.setViewportSize({ width, height });
    for (const mode of ["light", "dark"]) {
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value === "dark"),
        mode,
      );
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        true,
      );
      const card = await page.locator(".mcp-consent-card").boundingBox();
      assert.equal(card.width <= 680, true);
      await page.screenshot({
        path: `${screenshotPrefix}-consent-${size}-${mode}.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  readOnly = true;
  await page.reload();
  await page.locator(".mcp-consent-access").getByText("Read-only", { exact: true }).waitFor();
  assert.equal(await writeChoice.isDisabled(), true);
  assert.equal(
    await page.getByRole("heading", { name: "Update your account", exact: true }).count(),
    0,
  );
  readOnly = false;
  await page.reload();
  await page.getByText("Read and write", { exact: true }).waitFor();
  await page.setViewportSize(consentViewport);
  await page.evaluate(
    (value) => document.documentElement.classList.toggle("dark", value),
    consentTheme,
  );
  const setupUrl = extension ? `${origin}#/mcp` : new URL("/mcp", origin).href;
  await page.goto(setupUrl);
  const connectionsTab = page.getByRole("tab", { name: "Connected agents", exact: true });
  await connectionsTab.waitFor();
  assert.deepEqual(await page.getByRole("tab").allTextContents(), [
    "I'm a Human",
    "I'm an Agent",
    "Connected agents",
  ]);
  await page.getByRole("tab", { name: "I'm an Agent", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll('[role="tab"]')).some(
      (tab) =>
        tab.textContent === "Connected agents" && tab.getAttribute("aria-selected") === "true",
    ),
  );
  assert.equal(await connectionsTab.getAttribute("aria-selected"), "true");
  assert.equal(await page.getByRole("radio").count(), 0);
  const disconnect = page.getByRole("button", { name: "Disconnect Review client", exact: true });
  await disconnect.waitFor();
  assert.equal((await disconnect.getAttribute("class")).includes("button"), true);
  await page.screenshot({
    path: `${screenshotPrefix}-connections.png`,
    fullPage: true,
    animations: "disabled",
  });
  const connectionsViewport = page.viewportSize();
  const connectionsTheme = await page.evaluate(() =>
    document.documentElement.classList.contains("dark"),
  );
  for (const [size, width, height] of [
    ["desktop", 2048, 1100],
    ["mobile", 360, 800],
  ]) {
    await page.setViewportSize({ width, height });
    for (const mode of ["light", "dark"]) {
      await page.evaluate(
        (value) => document.documentElement.classList.toggle("dark", value === "dark"),
        mode,
      );
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        true,
      );
      assert.equal(await connectionsTab.isVisible(), true);
      assert.equal(await disconnect.isVisible(), true);
      await page.screenshot({
        path: `${screenshotPrefix}-connections-${size}-${mode}.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
  }
  await page.setViewportSize(connectionsViewport);
  await page.evaluate(
    (value) => document.documentElement.classList.toggle("dark", value),
    connectionsTheme,
  );
  await disconnect.click();
  await page.getByText("Agent disconnected.", { exact: true }).waitFor();
  assert.equal(await disconnect.count(), 0);
  assert.equal(connected, false);
  await page.getByText("No connected agents.", { exact: true }).waitFor();
  for (const access of ["read-only", "read-write"]) {
    approvedAccess = access;
    await page.goto(consentUrl);
    await page.getByRole("heading", { name: "Review client", exact: true }).waitFor();
    assert.equal(await writeChoice.isChecked(), true);
    if (access === "read-only") await readChoice.check();
    await page.getByRole("button", { name: "Allow connection", exact: true }).click();
    await page.waitForURL("https://client.callback.test/done");
  }
  await page.unroute("**/api/v1/user/auth/me", identity);
  await page.unroute(`**/api/v1/user/mcp/requests/${requestId}`, consent);
  await page.unroute("https://client.callback.test/done", callback);
  await page.unroute("**/api/v1/user/mcp/connections**", connections);
}

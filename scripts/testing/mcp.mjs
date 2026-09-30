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
  assert.equal(copiedPrompt.includes("https://agents.example.com/mcp"), true);
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
  const reference = page.locator(".mcp-reference summary");
  await reference.focus();
  await page.keyboard.press("Space");
  await page.getByRole("table", { name: "DevFeed MCP tools" }).waitFor();
  assert.equal(await page.getByRole("table").getByRole("row").count(), 15);
  await reference.click();
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
  await reference.click();
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    "The tool reference fits the mobile viewport",
  );
  await reference.click();
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
  const consentUrl = extension
    ? `${origin}#/mcp/authorize?request=review-request`
    : new URL("/mcp/authorize?request=review-request", origin).href;
  const identity = async (route) =>
    route.fulfill({
      json: {
        user_id: "mcp-reader",
        name: "Review reader",
        email: null,
        csrf_token: "review-csrf",
        expires_at: Date.now() / 1000 + 3600,
      },
    });
  const consent = async (route) => {
    if (route.request().method() === "POST") {
      assert.equal(route.request().headers()["x-csrf-token"], "review-csrf");
      assert.deepEqual(route.request().postDataJSON(), { approved: true });
      return route.fulfill({ json: { redirect_url: "https://client.callback.test/done" } });
    }
    return route.fulfill({
      json: {
        client_name: "Review client",
        scopes: ["devfeed:read", "devfeed:write"],
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
  await page.route("**/api/v1/user/mcp/requests/review-request", consent);
  await page.route("https://client.callback.test/done", callback);
  await page.route("**/api/v1/user/mcp/connections**", connections);
  await page.goto(consentUrl);
  if (extension) await page.reload();
  await page.getByRole("heading", { name: "Review client", exact: true }).waitFor();
  assert.equal(await page.getByText(/Save or remove bookmarks/).count(), 1);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
  );
  await page.screenshot({
    path: `${screenshotPrefix}-consent.png`,
    fullPage: true,
    animations: "disabled",
  });
  const setupUrl = extension ? `${origin}#/mcp` : new URL("/mcp", origin).href;
  await page.goto(setupUrl);
  const disconnect = page.getByRole("button", { name: "Disconnect Review client", exact: true });
  await disconnect.waitFor();
  assert.equal((await disconnect.getAttribute("class")).includes("button"), true);
  await page.screenshot({
    path: `${screenshotPrefix}-connections.png`,
    fullPage: true,
    animations: "disabled",
  });
  await disconnect.click();
  await page.getByText("Agent disconnected.", { exact: true }).waitFor();
  assert.equal(await disconnect.count(), 0);
  assert.equal(connected, false);
  await page.goto(consentUrl);
  await page.getByRole("heading", { name: "Review client", exact: true }).waitFor();
  await page.getByRole("button", { name: "Allow connection", exact: true }).click();
  await page.waitForURL("https://client.callback.test/done");
  await page.unroute("**/api/v1/user/auth/me", identity);
  await page.unroute("**/api/v1/user/mcp/requests/review-request", consent);
  await page.unroute("https://client.callback.test/done", callback);
  await page.unroute("**/api/v1/user/mcp/connections**", connections);
}

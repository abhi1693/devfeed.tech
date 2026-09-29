import assert from "node:assert/strict";

export const testMcpEndpoint = "https://mcp.ingress.test/devfeed/mcp";

export async function checkMcp(page, screenshotPrefix) {
  const original = page.url();
  const viewport = page.viewportSize();
  const theme = await page.evaluate(() => document.documentElement.classList.contains("dark"));
  const link = page
    .locator(".sidebar")
    .getByRole("link", { name: "Connect your agent", exact: true });
  await link.click();
  await page.getByRole("heading", { name: "Connect your agent", exact: true }).waitFor();
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
  assert.equal(await page.getByRole("table").getByRole("row").count(), 7);
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
  await page.screenshot({
    path: `${screenshotPrefix}-mobile.png`,
    fullPage: true,
    animations: "disabled",
  });
  await agent.click();
  await page.getByRole("radio", { name: /^Claude Code/ }).check();
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
  await page.setViewportSize(viewport);
  await page.goto(original);
  await page.getByRole("heading", { name: "Latest feed", exact: true }).waitFor();
  await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), theme);
}

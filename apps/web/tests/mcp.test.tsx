// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { McpContent } from "@/components/mcp-content";
import { mcpAgentPrompt, mcpConfiguration, mcpEndpoint } from "@/lib/mcp";

import * as runtime from "@/lib/reader-runtime";
const configuredEndpoint = "https://mcp.ingress.test/devfeed/mcp";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("keeps all available tools visible alongside either setup method", () => {
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  const reference = screen.getByRole("region", { name: "Available tools" });
  expect(reference.tagName).toBe("SECTION");
  expect(reference.querySelector("details")).toBeNull();
  expect(screen.getByRole("table", { name: "DevFeed MCP tools" })).toBeTruthy();
  expect(reference.querySelectorAll("tbody tr")).toHaveLength(14);
  fireEvent.click(screen.getByRole("tab", { name: "I'm an Agent" }));
  expect(screen.getByRole("table", { name: "DevFeed MCP tools" })).toBeTruthy();
});

it("uses each client's supported HTTP setup format", () => {
  const endpoint = "https://agents.example.com/mcp";
  expect(JSON.parse(mcpConfiguration("vscode", endpoint)!)).toEqual({
    servers: { devfeed: { type: "http", url: endpoint } },
  });
  expect(JSON.parse(mcpConfiguration("cursor", endpoint)!)).toEqual({
    mcpServers: { devfeed: { url: endpoint } },
  });
  expect(mcpConfiguration("codex", endpoint)).toBe(
    "codex mcp add devfeed --url 'https://agents.example.com/mcp'",
  );
  expect(mcpConfiguration("claude", endpoint)).toBe(
    "claude mcp add --transport http --scope local devfeed 'https://agents.example.com/mcp'",
  );
  expect(mcpConfiguration("other", endpoint)).toBe(endpoint);
});

it("quotes URLs containing shell metacharacters as literal arguments", () => {
  const endpoint = "https://agents.example.com/it's/mcp?value=$(id)";
  expect(mcpConfiguration("codex", endpoint)).toBe(
    "codex mcp add devfeed --url 'https://agents.example.com/it'\"'\"'s/mcp?value=$(id)'",
  );
});

it.each([
  "",
  "garbage",
  "javascript:alert(1)",
  "file:///mcp",
  "https://user:secret@example.com/mcp",
  "https://example.com/mcp#fragment",
])("rejects unusable endpoints in configurations and prompts: %s", (value) => {
  expect(mcpEndpoint(value)).toBeNull();
  expect(mcpConfiguration("codex", value)).toBeNull();
  expect(mcpAgentPrompt("claude", value)).toBeNull();
});

it("shows manual steps for all five tools", () => {
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  for (const name of ["VS Code", "Codex", "Claude Code", "Cursor", "Other"]) {
    fireEvent.click(screen.getByRole("radio", { name: new RegExp(`^${name}`) }));
    expect(screen.getByRole("heading", { name: `${name} setup` })).toBeTruthy();
  }
  fireEvent.click(screen.getByRole("radio", { name: /^Claude Code/ }));
  expect(screen.getByLabelText("MCP configuration").textContent).toContain(
    "--transport http --scope local devfeed",
  );
  expect(screen.getByText("Run this command in your project.")).toBeTruthy();
});

it("copies the current configuration and clears stale feedback after edits", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy configuration" }));
  await screen.findByText("Copied to clipboard.");
  expect(writeText).toHaveBeenCalledWith(mcpConfiguration("vscode", configuredEndpoint));
  fireEvent.click(screen.getByRole("radio", { name: /^Cursor/ }));
  expect(screen.queryByText("Copied to clipboard.")).toBeNull();
  fireEvent.change(screen.getByLabelText("MCP server URL"), { target: { value: "invalid" } });
  expect(screen.queryByRole("button", { name: "Copy configuration" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Copy address" })).toBeNull();
  expect(screen.getByLabelText("MCP server URL").getAttribute("aria-invalid")).toBe("true");
});

it("preserves the client and endpoint between keyboard-accessible human and agent tabs", async () => {
  const user = userEvent.setup();
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  fireEvent.click(screen.getByRole("radio", { name: /^Codex/ }));
  fireEvent.change(screen.getByLabelText("MCP server URL"), {
    target: { value: "https://agents.example.com/mcp" },
  });
  const human = screen.getByRole("tab", { name: "I'm a Human" });
  human.focus();
  await user.keyboard("{ArrowRight}");
  const agent = screen.getByRole("tab", { name: "I'm an Agent" });
  expect(agent.getAttribute("aria-selected")).toBe("true");
  expect(document.activeElement).toBe(agent);
  expect(screen.queryByLabelText("MCP configuration")).toBeNull();
  const prompt = screen.getByLabelText("Agent setup prompt").textContent;
  expect(prompt).toContain("Set up the DevFeed MCP server in Codex.");
  expect(prompt).toContain("https://agents.example.com/mcp");
  expect(prompt).toContain("codex mcp add devfeed");
  expect(prompt).toContain("Preserve other servers and settings.");
  expect(prompt).toContain("Only report success after a real tool call");
  await user.keyboard("{ArrowLeft}");
  expect(human.getAttribute("aria-selected")).toBe("true");
  expect(screen.getByLabelText("MCP configuration").textContent).toContain(
    "https://agents.example.com/mcp",
  );
});

it("copies a tool-specific setup prompt and suppresses it for an invalid URL", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  await user.click(screen.getByRole("tab", { name: "I'm an Agent" }));
  await user.click(screen.getByRole("radio", { name: /^Claude Code/ }));
  await user.click(screen.getByRole("button", { name: "Copy setup prompt" }));
  expect(writeText).toHaveBeenCalledWith(mcpAgentPrompt("claude", configuredEndpoint));
  fireEvent.change(screen.getByLabelText("MCP server URL"), { target: { value: "invalid" } });
  expect(screen.queryByRole("button", { name: "Copy setup prompt" })).toBeNull();
  expect(screen.queryByLabelText("Agent setup prompt")).toBeNull();
});

it("provides manual copying guidance if clipboard access fails", async () => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
  });
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy configuration" }));
  await screen.findByText("Couldn’t copy. Select the text and copy it manually.");
  expect(screen.getByLabelText("MCP configuration").textContent).toContain(configuredEndpoint);
});

it("keeps the URL empty when a deployment has not configured an endpoint", () => {
  render(<McpContent initialEndpoint={null} />);
  expect((screen.getByLabelText("MCP server URL") as HTMLInputElement).value).toBe("");
  expect(screen.getByText(/Enter your MCP server URL to get started/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Copy address" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Copy configuration" })).toBeNull();
  fireEvent.change(screen.getByLabelText("MCP server URL"), {
    target: { value: configuredEndpoint },
  });
  expect(screen.getByLabelText("MCP configuration").textContent).toContain(configuredEndpoint);
});

it("loads the deployment's runtime URL for bundled readers", async () => {
  const request = vi
    .spyOn(runtime, "readerRequest")
    .mockResolvedValue(Response.json({ url: configuredEndpoint }));
  render(<McpContent />);
  await waitFor(() =>
    expect((screen.getByLabelText("MCP server URL") as HTMLInputElement).value).toBe(
      configuredEndpoint,
    ),
  );
  expect(request).toHaveBeenCalledWith(
    "/api/v1/mcp/config",
    expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
  );
  expect(screen.getByLabelText("MCP configuration").textContent).toContain(configuredEndpoint);
});

it("keeps an entered URL when runtime configuration finishes loading", async () => {
  let resolve!: (value: Response) => void;
  vi.spyOn(runtime, "readerRequest").mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<McpContent />);
  const input = screen.getByLabelText("MCP server URL") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://my-server.test/mcp" } });
  await act(async () => {
    resolve(Response.json({ url: configuredEndpoint }));
  });
  expect(input.value).toBe("https://my-server.test/mcp");
});

it.each([Response.json({}, { status: 503 }), Response.json({ url: "file:///mcp" })])(
  "allows manual setup when runtime configuration cannot be loaded",
  async (response) => {
    vi.spyOn(runtime, "readerRequest").mockResolvedValue(response);
    render(<McpContent />);
    await screen.findByText("Couldn’t load the server URL. Enter it to continue.");
    expect((screen.getByLabelText("MCP server URL") as HTMLInputElement).value).toBe("");
    fireEvent.change(screen.getByLabelText("MCP server URL"), {
      target: { value: configuredEndpoint },
    });
    expect(screen.getByLabelText("MCP configuration").textContent).toContain(configuredEndpoint);
  },
);

it("shows each tool’s sign-in flow and copies the Codex login command", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  render(<McpContent initialEndpoint={configuredEndpoint} />);
  for (const name of ["VS Code", "Codex", "Claude Code", "Cursor", "Other"]) {
    await user.click(screen.getByRole("radio", { name: new RegExp(`^${name}`) }));
    await user.click(screen.getByText("Sign in to DevFeed"));
    expect(screen.getByText(/review permissions, and select Allow connection/)).toBeTruthy();
    expect(
      screen.getByText(`Return to ${name} and ask it to show your saved DevFeed articles.`),
    ).toBeTruthy();
    if (name === "Codex") {
      await user.click(screen.getByRole("button", { name: "Copy sign-in command" }));
      expect(writeText).toHaveBeenLastCalledWith("codex mcp login devfeed");
    }
    await user.click(screen.getByRole("tab", { name: "I'm an Agent" }));
    expect(screen.getByLabelText("Agent setup prompt").textContent).toContain("Allow connection");
    if (name === "Codex")
      expect(screen.getByLabelText("Agent setup prompt").textContent).toContain(
        "codex mcp login devfeed",
      );
    await user.click(screen.getByRole("tab", { name: "I'm a Human" }));
  }
});

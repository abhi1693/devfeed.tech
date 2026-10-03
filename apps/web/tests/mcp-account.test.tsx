// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { McpContent } from "@/components/mcp-content";
import { McpConsent } from "@/components/mcp-consent";
import { McpConnections } from "@/components/mcp-connections";
import userEvent from "@testing-library/user-event";
import * as userApi from "@/lib/user";

const state = vi.hoisted(() => ({
  session: {
    loading: false,
    user: { user_id: "one", csrf_token: "csrf", name: "Reader", email: null } as {
      user_id: string;
      csrf_token: string;
      name: string;
      email: null;
    } | null,
  },
}));
vi.mock("@/components/user-account", () => ({
  useUser: () => state.session,
  AccountGate: ({ returnTo }: { returnTo: string }) => <a href={returnTo}>Sign in</a>,
}));
vi.mock("@/components/user-login", () => ({
  UserLogin: ({ returnTo }: { returnTo: string }) => <a href={returnTo}>Sign in</a>,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  state.session = {
    loading: false,
    user: { user_id: "one", csrf_token: "csrf", name: "Reader", email: null },
  };
});

it("shows one server connection and includes account authorization in setup", async () => {
  vi.spyOn(userApi, "userRequest").mockResolvedValue({ items: [] });
  render(<McpContent initialEndpoint="https://mcp.test/mcp" />);
  expect((screen.getByLabelText("MCP server URL") as HTMLInputElement).value).toBe(
    "https://mcp.test/mcp",
  );
  expect(screen.queryByRole("button", { name: "My account" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Public discovery" })).toBeNull();
  expect(screen.getByRole("region", { name: "Server connection" }).querySelector("p")).toBeNull();
  expect(screen.getByText("Sign in to DevFeed")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "I'm an Agent" }));
  expect(screen.getByLabelText("Agent setup prompt").textContent).toContain(
    "let the client discover authorization metadata",
  );
  expect(screen.getByLabelText("Agent setup prompt").textContent).toContain("set_bookmark");
  fireEvent.click(screen.getByText("Available tools"));
  expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(15);
  expect((screen.getByLabelText("MCP server URL") as HTMLInputElement).value).toBe(
    "https://mcp.test/mcp",
  );
});

it("preserves the authorization request through browser sign-in", () => {
  state.session.user = null;
  render(<McpConsent requestId="request-one" />);
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe(
    "/mcp/authorize?request=request-one",
  );
});

it("shows requested permissions and sends consent with the session's CSRF token", async () => {
  const request = vi
    .spyOn(userApi, "userRequest")
    .mockResolvedValueOnce({
      client_name: "Codex",
      scopes: ["devfeed:read"],
      resource: "https://mcp.test/mcp",
      redirect_uri: "http://localhost:4321/callback",
    })
    .mockRejectedValueOnce(new Error("Expired"));
  render(<McpConsent requestId="request-one" />);
  await screen.findByRole("heading", { name: "Codex" });
  expect(screen.getByText("Server").nextElementSibling?.textContent).toBe("https://mcp.test/mcp");
  expect(screen.queryByText("http://localhost:4321/callback")).toBeNull();
  expect(screen.queryByText("Return to")).toBeNull();
  expect(
    screen.getByText("Read-only", { selector: ".mcp-consent-access, .mcp-connection-access" }),
  ).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Read your account" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Update your account" })).toBeNull();
  expect(screen.queryByText(/Save or remove bookmarks/)).toBeNull();
  expect((screen.getByRole("radio", { name: "Read-write" }) as HTMLInputElement).disabled).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "Allow connection" }));
  await screen.findByRole("alert");
  expect(request).toHaveBeenLastCalledWith(
    "mcp/requests/request-one",
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: '{"approved":true,"access":"read-only"}',
    }),
  );
});

it("shows an expired request without offering authorization actions", async () => {
  vi.spyOn(userApi, "userRequest").mockRejectedValue(new Error("Expired"));
  render(<McpConsent requestId="request-one" />);
  expect(screen.getByRole("status").textContent).toBe("Loading connection request…");
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Allow connection" })).toBeNull();
  expect(screen.getByRole("link", { name: "Back to setup" }).getAttribute("href")).toBe("/mcp");
});

it("shows write permissions and cancels the request with CSRF protection", async () => {
  const request = vi
    .spyOn(userApi, "userRequest")
    .mockResolvedValueOnce({
      client_name: "Codex",
      scopes: ["devfeed:read", "devfeed:write"],
      resource: "https://mcp.test/mcp",
      redirect_uri: "http://localhost:4321/callback",
    })
    .mockRejectedValueOnce(new Error("Retry"));
  render(<McpConsent requestId="request-one" />);
  await screen.findByRole("heading", { name: "Codex" });
  expect(screen.getByText("Read and write")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Update your account" })).toBeTruthy();
  expect(screen.getByText("Reader")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByRole("alert");
  expect(request).toHaveBeenLastCalledWith(
    "mcp/requests/request-one",
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: '{"approved":false}',
    }),
  );
});

it("disconnects an authorized agent and removes it from the account list", async () => {
  const request = vi
    .spyOn(userApi, "userRequest")
    .mockResolvedValueOnce({
      items: [
        { id: "grant", client_name: "Codex", scopes: ["devfeed:read"], expires_at: 2000000000 },
      ],
    })
    .mockResolvedValueOnce(undefined);
  render(<McpConnections />);
  fireEvent.click(await screen.findByRole("button", { name: "Disconnect Codex" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Disconnect Codex" })).toBeNull(),
  );
  expect(request).toHaveBeenLastCalledWith("mcp/connections/grant", {
    method: "DELETE",
    headers: { "X-CSRF-Token": "csrf" },
  });
});

it("opens account connections in the third tab without setup controls", async () => {
  const request = vi.spyOn(userApi, "userRequest").mockResolvedValue({
    items: [
      {
        id: "one",
        client_name: "Codex",
        scopes: ["devfeed:read", "devfeed:write"],
        expires_at: 2000000000,
      },
      { id: "two", client_name: "Claude Code", scopes: ["devfeed:read"], expires_at: 2000000000 },
    ],
  });
  render(<McpContent initialEndpoint="https://mcp.test/mcp" />);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "I'm a Human",
    "I'm an Agent",
    "Connected agents",
  ]);
  expect(request).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("tab", { name: "Connected agents" }));
  await screen.findByRole("button", { name: "Disconnect Codex" });
  expect(screen.getByRole("button", { name: "Disconnect Claude Code" })).toBeTruthy();
  expect(
    screen.getByText("Read-only", { selector: ".mcp-consent-access, .mcp-connection-access" }),
  ).toBeTruthy();
  expect(screen.getByText("Read and write")).toBeTruthy();
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.queryByLabelText("MCP configuration")).toBeNull();
  await userEvent.click(screen.getByRole("tab", { name: "I'm an Agent" }));
  expect(screen.getAllByRole("radio")).toHaveLength(5);
  expect(screen.queryByRole("button", { name: "Disconnect Codex" })).toBeNull();
});

it("does not show account management while signed out", () => {
  state.session.user = null;
  render(<McpContent initialEndpoint="https://mcp.test/mcp" />);
  expect(screen.queryByRole("tab", { name: "Connected agents" })).toBeNull();
});

it("shows connection loading and failure without claiming the account has no agents", async () => {
  vi.spyOn(userApi, "userRequest").mockRejectedValue(new Error("Unavailable"));
  render(<McpConnections />);
  expect(screen.getByRole("status").textContent).toBe("Loading connected agents…");
  expect(screen.queryByText("No connected agents.")).toBeNull();
  await screen.findByRole("alert");
  expect(screen.queryByText("No connected agents.")).toBeNull();
});

it.each(["read-only", "read-write"])(
  "defaults to read-write, updates capabilities and approves %s",
  async (access) => {
    const request = vi
      .spyOn(userApi, "userRequest")
      .mockResolvedValueOnce({
        client_name: "Codex",
        scopes: ["devfeed:read", "devfeed:write", "offline_access"],
        resource: "https://mcp.test/mcp",
        redirect_uri: "http://localhost:4321/callback",
      })
      .mockRejectedValueOnce(new Error("Retry"));
    render(<McpConsent requestId="request-one" />);
    const write = await screen.findByRole("radio", { name: "Read-write" });
    const read = screen.getByRole("radio", { name: "Read-only" });
    expect((write as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("heading", { name: "Update your account" })).toBeTruthy();
    await userEvent.click(read);
    expect(screen.queryByRole("heading", { name: "Update your account" })).toBeNull();
    expect(screen.getByText(/this agent cannot change/)).toBeTruthy();
    if (access === "read-write") {
      await userEvent.click(write);
      expect(screen.getByRole("heading", { name: "Update your account" })).toBeTruthy();
    }
    await userEvent.click(screen.getByRole("button", { name: "Allow connection" }));
    await screen.findByRole("alert");
    expect(request).toHaveBeenLastCalledWith(
      "mcp/requests/request-one",
      expect.objectContaining({ body: JSON.stringify({ approved: true, access }) }),
    );
    expect(access === "read-only" ? read : (write as HTMLInputElement)).toHaveProperty(
      "checked",
      true,
    );
  },
);

it("resets the selection for a new request and hides stale approval controls", async () => {
  const details = {
    client_name: "Codex",
    scopes: ["devfeed:read", "devfeed:write"],
    resource: "https://mcp.test/mcp",
    redirect_uri: "http://localhost:4321/callback",
  };
  let finish!: (value: typeof details) => void;
  vi.spyOn(userApi, "userRequest")
    .mockResolvedValueOnce(details)
    .mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
  const view = render(<McpConsent requestId="request-one" />);
  await screen.findByRole("radio", { name: "Read-write" });
  await userEvent.click(screen.getByRole("radio", { name: "Read-only" }));
  view.rerender(<McpConsent requestId="request-two" />);
  expect(screen.queryByRole("button", { name: "Allow connection" })).toBeNull();
  finish(details);
  const write = await screen.findByRole("radio", { name: "Read-write" });
  expect((write as HTMLInputElement).checked).toBe(true);
});

it("keeps the selected permission when the same browser session refreshes", async () => {
  const request = vi.spyOn(userApi, "userRequest").mockResolvedValue({
    client_name: "Codex",
    scopes: ["devfeed:read", "devfeed:write"],
    resource: "https://mcp.test/mcp",
    redirect_uri: "http://localhost:4321/callback",
  });
  const view = render(<McpConsent requestId="request-one" />);
  await screen.findByRole("radio", { name: "Read-write" });
  await userEvent.click(screen.getByRole("radio", { name: "Read-only" }));
  state.session = {
    loading: false,
    user: { user_id: "one", csrf_token: "new-csrf", name: "Reader", email: null },
  };
  view.rerender(<McpConsent requestId="request-one" />);
  expect((screen.getByRole("radio", { name: "Read-only" }) as HTMLInputElement).checked).toBe(true);
  expect(request).toHaveBeenCalledTimes(1);
});

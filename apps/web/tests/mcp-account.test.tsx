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
  expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(14);
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
  expect(screen.queryByText(/Save or remove bookmarks/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Allow connection" }));
  await screen.findByRole("alert");
  expect(request).toHaveBeenLastCalledWith(
    "mcp/requests/request-one",
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      body: '{"approved":true}',
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

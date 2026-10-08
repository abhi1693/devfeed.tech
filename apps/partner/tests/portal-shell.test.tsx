// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { PortalShell } from "../src/app/portal-shell";

afterEach(cleanup);
const identity = { subject: "alice", name: "Alice", roles: ["partner"], csrf_token: "csrf" };

it("shows partner navigation without management access", () => {
  render(
    <PortalShell identity={identity} onSignOut={vi.fn()}>
      <h1>Overview content</h1>
    </PortalShell>,
  );
  expect(screen.getByRole("navigation", { name: "Partner portal" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Manage partnerships" })).toBeNull();
  expect(screen.getByRole("link", { name: "Products & ads" }).getAttribute("href")).toBe("/assets");
});

it("opens mobile navigation and closes it after choosing a section", async () => {
  const user = userEvent.setup();
  render(
    <PortalShell identity={{ ...identity, roles: ["superuser"] }} onSignOut={vi.fn()}>
      <h1>Overview content</h1>
    </PortalShell>,
  );
  const toggle = screen.getByRole("button", { name: "Navigation" });
  await user.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByRole("link", { name: "Performance" }));
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
});

it("opens the account dropdown with identity, scoped navigation and sign out", async () => {
  const signOut = vi.fn().mockResolvedValue(undefined);
  render(
    <PortalShell
      identity={{ ...identity, name: "Alice Partner", email: "alice@example.test" }}
      query="?account=alpha&days=7"
      onSignOut={signOut}
    >
      <h1>Overview</h1>
    </PortalShell>,
  );
  expect(screen.queryByRole("menuitem", { name: "Sign out" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "User menu: Alice Partner" }));
  expect(screen.getByText("alice@example.test")).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Performance" }).getAttribute("href")).toBe(
    "/performance?account=alpha&days=7",
  );
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(signOut).toHaveBeenCalledTimes(1);
});

it("keeps failed sign out retryable inside the account menu", async () => {
  const signOut = vi.fn().mockRejectedValue(new Error("Offline"));
  render(
    <PortalShell identity={identity} onSignOut={signOut}>
      <h1>Overview</h1>
    </PortalShell>,
  );
  await userEvent.click(screen.getByRole("button", { name: "User menu: Alice" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(signOut).toHaveBeenCalledTimes(2);
});

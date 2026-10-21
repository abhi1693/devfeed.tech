// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PortalShell } from "../src/app/portal-shell";
import { partnerIdentity } from "./partner-fixtures";

beforeEach(() => localStorage.clear());
afterEach(cleanup);
const identity = partnerIdentity();

it("shows partner navigation without management access", () => {
  render(
    <PortalShell identity={identity} onSignOut={vi.fn()}>
      <h1>Overview content</h1>
    </PortalShell>,
  );
  expect(screen.getByRole("navigation", { name: "Partner portal" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Manage partnerships" })).toBeNull();
  expect(screen.queryByText("Partners")).toBeNull();
  expect(screen.queryByText("DevFeed Partners")).toBeNull();
  const products = screen.getByRole("link", { name: "Products" });
  expect(products.getAttribute("href")).toBe("/products");
  expect(products.getAttribute("title")).toBe("Products");
  expect(products.getAttribute("aria-label")).toBe("Products");
  expect(products.querySelector("svg")?.getAttribute("width")).toBe("20");
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
      query="?days=7"
      onSignOut={signOut}
    >
      <h1>Overview</h1>
    </PortalShell>,
  );
  expect(screen.queryByRole("menuitem", { name: "Sign out" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "User menu: Alice Partner" }));
  expect(screen.getByText("alice@example.test")).toBeTruthy();
  expect(screen.getByRole("menuitem", { name: "Performance" }).getAttribute("href")).toBe(
    "/performance?days=7",
  );
  expect(
    screen.queryByText("Also ends your SSO session; other apps may sign you out.", { exact: true }),
  ).toBeNull();
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

it("uses the shared collapsed sidebar and restores its expansion after remounting", async () => {
  const view = render(
    <PortalShell identity={identity} onSignOut={vi.fn()}>
      <h1>Overview</h1>
    </PortalShell>,
  );
  const sidebar = screen.getByRole("complementary", { name: "Partner navigation" });
  expect(sidebar.getAttribute("data-expanded")).toBe("false");
  expect(sidebar.getAttribute("data-sidebar-layout")).toBe("workspace");
  const expand = screen.getByRole("button", { name: "Expand sidebar" });
  expand.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(sidebar.getAttribute("data-expanded")).toBe("true"));
  expect(localStorage.getItem("devfeed:partner-sidebar-expanded")).toBe("true");
  view.unmount();
  render(
    <PortalShell identity={identity} onSignOut={vi.fn()} section="performance" query="?days=7">
      <h1>Performance</h1>
    </PortalShell>,
  );
  const restored = screen.getByRole("complementary", { name: "Partner navigation" });
  await waitFor(() => expect(restored.getAttribute("data-expanded")).toBe("true"));
  const navigation = screen.getByRole("navigation", { name: "Partner portal" });
  const active = within(navigation).getByRole("link", { name: "Performance" });
  expect(active.getAttribute("aria-current")).toBe("page");
  expect(active.getAttribute("href")).toBe("/performance?days=7");
  await userEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
  expect(restored.getAttribute("data-expanded")).toBe("false");
  expect(localStorage.getItem("devfeed:partner-sidebar-expanded")).toBe("false");
});
it("keeps mobile disclosure and Escape independent from the saved desktop width", async () => {
  localStorage.setItem("devfeed:partner-sidebar-expanded", "true");
  render(
    <PortalShell identity={identity} onSignOut={vi.fn()}>
      <h1>Overview</h1>
    </PortalShell>,
  );
  const sidebar = screen.getByRole("complementary", { name: "Partner navigation" });
  const mobile = screen.getByRole("button", { name: "Navigation" });
  await userEvent.click(mobile);
  expect(sidebar.getAttribute("data-mobile-open")).toBe("true");
  screen.getByRole("link", { name: "Products" }).focus();
  await userEvent.keyboard("{Escape}");
  expect(mobile.getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(mobile);
  expect(sidebar.getAttribute("data-expanded")).toBe("true");
  expect(localStorage.getItem("devfeed:partner-sidebar-expanded")).toBe("true");
});

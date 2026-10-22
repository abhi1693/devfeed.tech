// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UserProvider } from "@/components/user-account";
import { UserShell } from "@/components/user-shell";
import { parseFilters, type FeedFilters } from "@/lib/feed-query";
import type { UserIdentity } from "@/lib/user";

vi.mock("next/image", () => ({ default: () => null }));
vi.mock("@/components/user-search", () => ({ UserSearch: () => null }));
vi.mock("@/components/notification-inbox", () => ({ NotificationInbox: () => null }));
vi.mock("@/components/must-reads", () => ({ MustReads: () => null }));
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }));

const reader: UserIdentity = {
  user_id: "reader",
  name: "Reader",
  email: null,
  csrf_token: "csrf",
  expires_at: 4102444800,
};
let identity: UserIdentity | null;
const network = vi.fn();
type Section = NonNullable<Parameters<typeof UserShell>[0]["section"]>;

function App({ section = "feed", filters }: { section?: Section; filters?: FeedFilters }) {
  return (
    <UserProvider>
      <UserShell section={section} filters={filters}>
        Articles
      </UserShell>
    </UserProvider>
  );
}

function mobile() {
  return within(screen.getByRole("navigation", { name: "Mobile navigation" }));
}

function desktop() {
  return within(screen.getByRole("complementary", { name: "Primary navigation" }));
}

async function signedIn() {
  await waitFor(() => expect(screen.getAllByRole("link", { name: "My feed" })).toHaveLength(2));
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.sidebarState;
  identity = reader;
  network
    .mockReset()
    .mockImplementation(async (url: string) =>
      Response.json(
        url.endsWith("auth/me") ? identity : { display_name: "Reader", avatar_url: null },
      ),
    );
  vi.stubGlobal("fetch", network);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.sidebarState;
});

it.each(["guest", "member"])("keeps Appearance out of the top bar for a %s", async (owner) => {
  identity = owner === "member" ? reader : null;
  render(<App />);
  const header = within(screen.getByRole("banner"));
  expect(header.queryByRole("link", { name: /Appearance/ })).toBeNull();
  await act(async () => {});
  expect(header.queryByRole("link", { name: /Appearance/ })).toBeNull();
  if (owner === "guest") expect(header.getByRole("link", { name: "Sign in" })).toBeTruthy();
  else {
    await signedIn();
    act(() => window.dispatchEvent(new Event("devfeed:user-session-expired")));
    expect(header.queryByRole("link", { name: /Appearance/ })).toBeNull();
  }
});

it("keeps six direct mobile destinations and retains secondary destinations on desktop", async () => {
  render(<App />);
  await signedIn();
  expect(
    mobile()
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual(["My feed", "Latest", "Read later", "Topics", "Tags", "Sources"]);
  expect(
    mobile()
      .getAllByRole("link")
      .map((link) => link.getAttribute("href")),
  ).toEqual(["/", "/latest", "/read-later", "/topics", "/tags", "/sources"]);
  expect(mobile().queryByRole("link", { name: "Connect your agent" })).toBeNull();
  expect(mobile().queryByRole("link", { name: "Leaderboard" })).toBeNull();
  expect(desktop().getByRole("link", { name: "Connect your agent" }).getAttribute("href")).toBe(
    "/mcp",
  );
  expect(desktop().getByRole("link", { name: "Leaderboard" }).getAttribute("href")).toBe(
    "/leaderboard",
  );
});

it("shows only public mobile destinations to guests and hides Read later on both layouts", async () => {
  identity = null;
  render(<App />);
  await act(async () => {});
  expect(
    mobile()
      .getAllByRole("link")
      .map((link) => link.textContent),
  ).toEqual(["Latest", "Topics", "Tags", "Sources"]);
  for (const navigation of [mobile(), desktop()]) {
    expect(navigation.queryByRole("link", { name: "Read later" })).toBeNull();
    expect(navigation.queryByRole("link", { name: "My feed" })).toBeNull();
  }
  expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toContain("/login?");
  expect(network.mock.calls.every(([url]) => !url.includes("auth/login"))).toBe(true);
});

it("removes private destinations in both layouts when the session expires", async () => {
  render(<App section="bookmarks" />);
  await signedIn();
  expect(mobile().getByRole("link", { name: "Read later" }).getAttribute("aria-current")).toBe(
    "page",
  );
  act(() => window.dispatchEvent(new Event("devfeed:user-session-expired")));
  expect(mobile().getAllByRole("link")).toHaveLength(4);
  for (const navigation of [mobile(), desktop()]) {
    expect(navigation.queryByRole("link", { name: "Read later" })).toBeNull();
    expect(navigation.queryByRole("link", { name: "My feed" })).toBeNull();
  }
});

it.each([
  ["personal", "My feed"],
  ["feed", "Latest"],
  ["bookmarks", "Read later"],
  ["topics", "Topics"],
  ["tags", "Tags"],
  ["sources", "Sources"],
] as const)("marks only the current mobile destination for %s", async (section, label) => {
  render(<App section={section} />);
  await signedIn();
  expect(mobile().getByRole("link", { name: label }).getAttribute("aria-current")).toBe("page");
  expect(
    mobile()
      .getAllByRole("link")
      .filter((link) => link.getAttribute("aria-current") === "page"),
  ).toHaveLength(1);
});

it("does not mark Latest as the current destination on a filtered topic page", async () => {
  render(<App filters={parseFilters({ topic: "typescript" })} />);
  await signedIn();
  expect(mobile().getByRole("link", { name: "Latest" }).getAttribute("aria-current")).toBeNull();
});

it("keeps every direct mobile destination in the native keyboard navigation order", async () => {
  const user = userEvent.setup();
  render(<App />);
  await signedIn();
  const links = mobile().getAllByRole("link");
  links[0].focus();
  for (const link of links.slice(1)) {
    await user.tab();
    expect(document.activeElement).toBe(link);
  }
});

it("uses the shared sidebar while retaining saved reader preference and legal navigation", async () => {
  localStorage.setItem("devfeed:sidebar-expanded", "true");
  const user = userEvent.setup();
  render(<App />);
  await signedIn();
  const sidebar = screen.getByRole("complementary", { name: "Primary navigation" });
  expect(sidebar.id).toBe("reader-sidebar");
  expect(sidebar.getAttribute("data-sidebar-layout")).toBe("reader");
  expect(document.documentElement.dataset.sidebarState).toBe("expanded");
  expect(within(sidebar).getByRole("navigation", { name: "Legal" })).toBeTruthy();
  await user.click(desktop().getByRole("button", { name: "Collapse sidebar" }));
  expect(localStorage.getItem("devfeed:sidebar-expanded")).toBe("false");
  expect(document.documentElement.dataset.sidebarState).toBe("collapsed");
  await user.click(desktop().getByRole("button", { name: "Expand sidebar" }));
  expect(localStorage.getItem("devfeed:sidebar-expanded")).toBe("true");
});

// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Portal } from "../src/app/portal";
import type { Dashboard } from "../src/lib/types";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const account = {
  id: "alpha",
  name: "Alpha",
  tier: "gold",
  benefits: ["Product placements"],
  status: "active" as const,
};
const identity = { subject: "alice", name: "Alice", roles: ["partner"], csrf_token: "csrf" };
const dashboard: Dashboard = {
  account,
  start: "2026-10-01",
  end: "2026-10-07",
  totals: { impressions: 100, clicks: 5, ctr: 5, measured_days: 1 },
  assets: [],
  asset_total: 0,
  trend: [],
  last_updated_at: null,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the tier and benefits on overview without duplicated performance or a single-account selector", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(dashboard)));
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
  expect(await screen.findByText("Gold")).toBeTruthy();
  expect(screen.queryByText("5.00%")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Performance" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Reporting period" })).toBeNull();
  expect(screen.queryByText("Manage partnerships")).toBeNull();
  expect(window.location.pathname).toBe("/alpha");
  expect(new URLSearchParams(window.location.search).has("account")).toBe(false);
});

it("distinguishes unavailable measurements from measured zero", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    Response.json({
      ...dashboard,
      totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 0 },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="performance"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  expect(await screen.findByText(/No delivery measurements/)).toBeTruthy();
  fetchMock.mockResolvedValue(
    Response.json({
      ...dashboard,
      totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 1 },
    }),
  );
  await userEvent.click(screen.getByText("Refresh"));
  await waitFor(() => expect(screen.queryByText(/No delivery measurements/)).toBeNull());
  expect(screen.getAllByText("0").length).toBe(2);
});

it("changes account scope and period together with the reporting request", async () => {
  const beta = { ...account, id: "beta", name: "Beta" };
  const fetchMock = vi
    .fn()
    .mockImplementation((url: string) =>
      Promise.resolve(
        Response.json({ ...dashboard, account: url.includes("beta") ? beta : account }),
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="performance"
      identity={identity}
      initialAccounts={{ items: [account, beta], total: 2 }}
    />,
  );
  await screen.findByText("5.00%");
  await userEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await userEvent.type(screen.getByPlaceholderText("Search partner account…"), "Beta");
  await userEvent.click(screen.getByRole("option", { name: "Beta" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Reporting period" }));
  await userEvent.click(screen.getByRole("option", { name: "Last 7 days" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/partner/accounts/beta/dashboard?days=7&offset=0",
      expect.anything(),
    ),
  );
});

it("explains role access without memberships", () => {
  vi.stubGlobal("fetch", vi.fn());
  render(<Portal identity={identity} initialAccounts={{ items: [], total: 0 }} />);
  expect(screen.getByText(/no active partner membership/)).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});

it("removes reporting data when a request loses membership", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByText("Gold")).toBeNull();
});

it("keeps account selection when the account list is paginated", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(dashboard)));
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 101 }} />);
  await screen.findByText("Gold");
  expect(screen.getByRole("combobox", { name: "Partner account" })).toBeTruthy();
});

it("defaults a single account on performance even if a different initial selection is supplied", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json(dashboard));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="performance"
      identity={identity}
      initialSelected="foreign"
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText("5.00%");
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(screen.getByRole("combobox", { name: "Reporting period" })).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/partner/accounts/alpha/dashboard?days=30&offset=0",
    expect.anything(),
  );
});

it.each([401, 403])(
  "returns to sign-in when reporting rejects the session (%s)",
  async (status) => {
    router.replace.mockClear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));
    render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Gold")).toBeNull();
  },
);

it("switches account pages without retaining the previous account's reporting data", async () => {
  const beta = { ...account, id: "beta", name: "Beta", benefits: ["Beta benefits"] };
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    if (url.includes("accounts?offset=100")) return Response.json({ items: [beta], total: 101 });
    if (url.includes("accounts?offset=0")) return Response.json({ items: [account], total: 101 });
    return Response.json({ ...dashboard, account: url.includes("/beta/") ? beta : account });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 101 }} />);
  await screen.findByText("Product placements");
  await userEvent.click(screen.getByRole("button", { name: "Next accounts" }));
  await screen.findByText("Beta benefits");
  expect(screen.queryByText("Product placements")).toBeNull();
  expect(window.location.pathname).toBe("/beta");
  await userEvent.click(screen.getByRole("button", { name: "Previous accounts" }));
  await screen.findByText("Product placements");
  expect(screen.queryByText("Beta benefits")).toBeNull();
});

it("keeps sign-out errors visible and retries with the session CSRF token", async () => {
  router.replace.mockClear();
  let rejected = true;
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith("/logout")) return new Response(null, { status: rejected ? 503 : 204 });
    return Response.json(dashboard);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
  await screen.findByText("Gold");
  await userEvent.click(screen.getByRole("button", { name: "User menu: Alice" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await screen.findByText("Could not sign out. Please try again.");
  expect(router.replace).not.toHaveBeenCalled();
  rejected = false;
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/partner/auth/logout", {
    method: "POST",
    headers: { "x-csrf-token": "csrf" },
  });
});

it("renders daily reporting rows and measured asset metrics", async () => {
  const data = {
    ...dashboard,
    trend: [{ day: "2026-10-01", impressions: 100, clicks: 5 }],
    last_updated_at: "2026-10-01T00:00:00Z",
    assets: [
      {
        id: "asset",
        name: "API tool",
        kind: "product",
        status: "active",
        measured_days: 1,
        impressions: 100,
        clicks: 5,
        ctr: 5,
      },
    ],
    asset_total: 1,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () => Response.json(data)),
  );
  const view = render(
    <Portal
      section="performance"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText("2026-10-01");
  expect(screen.getByText(/Last measurement update/)).toBeTruthy();
  view.unmount();
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText("API tool");
  expect(screen.getByText("5.00%")).toBeTruthy();
});

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

it("shows measured outcomes and tier without exposing management to partners", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(dashboard)));
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
  expect(await screen.findByText("Gold")).toBeTruthy();
  expect(screen.getByText("5.00%")).toBeTruthy();
  expect(screen.queryByText("Manage partnerships")).toBeNull();
});

it("distinguishes unavailable measurements from measured zero", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    Response.json({
      ...dashboard,
      totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 0 },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<Portal identity={identity} initialAccounts={{ items: [account], total: 1 }} />);
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
  render(<Portal identity={identity} initialAccounts={{ items: [account, beta], total: 2 }} />);
  await screen.findByText("Gold");
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

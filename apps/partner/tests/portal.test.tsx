// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

it("shows ledger format totals independently of one grouped product row", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        ...dashboard,
        format_totals: [
          { ad_format: "feed", impressions: 90, clicks: 3, ctr: 3.33, measured_days: 1 },
          {
            ad_format: "homepage_sponsorship",
            impressions: 10,
            clicks: 2,
            ctr: 20,
            measured_days: 1,
          },
        ],
      }),
    ),
  );
  render(
    <Portal
      section="performance"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  const breakdown = await screen.findByRole("region", { name: "Performance by format" });
  expect(within(breakdown).getByText("Homepage sponsorship")).toBeTruthy();
  expect(within(breakdown).getByText("20.00%")).toBeTruthy();
  expect(within(breakdown).getAllByRole("row")).toHaveLength(3);
});

it("shows every delivered format for a grouped product without adding a Type column", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        ...dashboard,
        assets: [
          {
            id: "group-1",
            name: "CronWatch",
            kind: "ad",
            status: "active",
            product_id: null,
            ad_formats: ["feed", "homepage_sponsorship", "engagement"],
            impressions: 100,
            clicks: 5,
            ctr: 5,
            measured_days: 1,
          },
        ],
        asset_total: 1,
      }),
    ),
  );
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText("CronWatch");
  expect(screen.getByText("Feed · Homepage sponsorship · Engagement")).toBeTruthy();
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.queryByRole("columnheader", { name: "Type" })).toBeNull();
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
      "/api/v1/partner/accounts/beta/dashboard?days=7&offset=0&sort=name&order=asc",
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

it("shows an authorized off-page account without changing the picker page or its reporting scope", async () => {
  const bookmarked = {
    ...account,
    id: "bookmarked",
    name: "Bookmarked partner",
    benefits: ["Bookmarked benefits"],
  };
  const pickerPage = Array.from({ length: 100 }, (_, index) => ({
    ...account,
    id: `other-${index}`,
    name: `Other partner ${index}`,
  }));
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ ...dashboard, account: bookmarked }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      identity={identity}
      initialAccount={bookmarked}
      initialAccounts={{ items: pickerPage, total: 205 }}
    />,
  );
  await screen.findByText("Bookmarked benefits");
  const picker = screen.getByRole("combobox", { name: "Partner account" });
  expect(picker.textContent).toContain("Bookmarked partner");
  expect(screen.getByText("1–100 of 205")).toBeTruthy();
  await userEvent.click(picker);
  expect(screen.getAllByRole("option")).toHaveLength(100);
  expect(screen.queryByRole("option", { name: "Bookmarked partner" })).toBeNull();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/partner/accounts/bookmarked/dashboard?days=30&offset=0&sort=name&order=asc",
    expect.anything(),
  );
  expect(fetchMock.mock.calls.filter(([url]) => url.includes("/dashboard?"))).toHaveLength(1);
  expect(window.location.pathname).toBe("/bookmarked");
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

it("preserves reporting while paging accounts and changes scope only after selecting another account", async () => {
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
  await screen.findByText("101–101 of 101");
  expect(screen.getByText("Product placements")).toBeTruthy();
  expect(screen.queryByText("Beta benefits")).toBeNull();
  expect(screen.getByRole("combobox", { name: "Partner account" }).textContent).toContain("Alpha");
  expect(window.location.pathname).toBe("/alpha");
  expect(fetchMock.mock.calls.some(([url]) => url.includes("/beta/dashboard"))).toBe(false);
  await userEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await userEvent.click(screen.getByRole("option", { name: "Beta" }));
  await screen.findByText("Beta benefits");
  expect(screen.queryByText("Product placements")).toBeNull();
  expect(window.location.pathname).toBe("/beta");
  await userEvent.click(screen.getByRole("button", { name: "Previous accounts" }));
  await screen.findByText("1–100 of 101");
  expect(screen.getByText("Beta benefits")).toBeTruthy();
  expect(screen.queryByText("Product placements")).toBeNull();
  expect(screen.getByRole("combobox", { name: "Partner account" }).textContent).toContain("Beta");
  expect(window.location.pathname).toBe("/beta");
  await userEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await userEvent.click(screen.getByRole("option", { name: "Alpha" }));
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

const asset = {
  id: "asset-1",
  name: "API tool",
  kind: "product" as const,
  product_id: "product-1",
  status: "active" as const,
  measured_days: 1,
  impressions: 100,
  clicks: 5,
  ctr: 5,
};

it("sorts all four reporting headers in both directions and removes the Type column", async () => {
  const fetchMock = vi
    .fn()
    .mockImplementation(async () =>
      Response.json({ ...dashboard, assets: [asset], asset_total: 1 }),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText(asset.name);
  expect(screen.queryByRole("columnheader", { name: "Type" })).toBeNull();
  expect(screen.getByRole("columnheader", { name: "Name" }).getAttribute("aria-sort")).toBe(
    "ascending",
  );
  for (const [field, label] of [
    ["name", "Name"],
    ["impressions", "Impressions"],
    ["clicks", "Clicks"],
    ["ctr", "CTR"],
  ]) {
    for (const order of ["desc", "asc"]) {
      await userEvent.click(screen.getByRole("button", { name: `Sort by ${label}` }));
      await waitFor(() =>
        expect(fetchMock).toHaveBeenLastCalledWith(
          `/api/v1/partner/accounts/alpha/dashboard?days=30&offset=0&sort=${field}&order=${order}`,
          expect.anything(),
        ),
      );
      expect(screen.getByRole("columnheader", { name: label }).getAttribute("aria-sort")).toBe(
        order === "asc" ? "ascending" : "descending",
      );
      expect(document.activeElement).toBe(screen.getByRole("button", { name: `Sort by ${label}` }));
    }
  }
  await userEvent.click(screen.getByRole("button", { name: "Sort by Name" }));
  expect(screen.getByRole("columnheader", { name: "Name" }).getAttribute("aria-sort")).toBe(
    "ascending",
  );
});

it("restores sort and page scope, preserves it in navigation and resets pagination for a new sort", async () => {
  const fetchMock = vi.fn().mockImplementation(async () =>
    Response.json({
      ...dashboard,
      assets: [asset, { ...asset, id: "asset-2", name: "Another tool" }],
      asset_total: 201,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
      initialDays={7}
      initialSort="ctr"
      initialOrder="desc"
      initialAssetOffset={100}
    />,
  );
  await screen.findByText(asset.name);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts/alpha/dashboard?days=7&offset=100&sort=ctr&order=desc",
    expect.anything(),
  );
  expect(screen.getByRole("link", { name: "Performance" }).getAttribute("href")).toBe(
    "/alpha/performance?days=7&sort=ctr&order=desc&asset_offset=100",
  );
  expect(new URLSearchParams(window.location.search).get("asset_offset")).toBe("100");
  // The API owns global ordering; the frontend must preserve the returned page order.
  expect(
    within(screen.getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelector("[data-asset-name]")?.textContent),
  ).toEqual(["API tool", "Another tool"]);
  await userEvent.click(screen.getByRole("button", { name: "Sort by Impressions" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/v1/partner/accounts/alpha/dashboard?days=7&offset=0&sort=impressions&order=desc",
      expect.anything(),
    ),
  );
  const scope = new URLSearchParams(window.location.search);
  expect(scope.get("sort")).toBe("impressions");
  expect(scope.get("order")).toBe("desc");
  expect(scope.has("asset_offset")).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText(asset.name);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts/alpha/dashboard?days=7&offset=100&sort=impressions&order=desc",
    expect.anything(),
  );
});

it("recovers a saved asset page after the available inventory shrinks", async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string) =>
    Response.json({
      ...dashboard,
      assets: url.includes("offset=100") ? [] : [asset],
      asset_total: 50,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
      initialSort="clicks"
      initialOrder="desc"
      initialAssetOffset={100}
    />,
  );
  await screen.findByText(asset.name);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts/alpha/dashboard?days=30&offset=0&sort=clicks&order=desc",
    expect.anything(),
  );
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(new URLSearchParams(window.location.search).has("asset_offset")).toBe(false);
  expect(screen.getByRole("button", { name: "Sort by Clicks" })).toBeTruthy();
});

it("resets asset pagination when the reporting period or account changes while retaining sorting", async () => {
  const beta = { ...account, id: "beta", name: "Beta" };
  const fetchMock = vi
    .fn()
    .mockImplementation(async () =>
      Response.json({ ...dashboard, assets: [asset], asset_total: 201 }),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account, beta], total: 2 }}
      initialSort="clicks"
      initialOrder="desc"
      initialAssetOffset={100}
    />,
  );
  await screen.findByText(asset.name);
  await userEvent.click(screen.getByRole("combobox", { name: "Reporting period" }));
  await userEvent.click(screen.getByRole("option", { name: "Last 7 days" }));
  await screen.findByText(asset.name);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts/alpha/dashboard?days=7&offset=0&sort=clicks&order=desc",
    expect.anything(),
  );
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText(asset.name);
  await userEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await userEvent.click(screen.getByRole("option", { name: "Beta" }));
  await screen.findByText(asset.name);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/partner/accounts/beta/dashboard?days=7&offset=0&sort=clicks&order=desc",
    expect.anything(),
  );
});

it("preserves sorting focus while loading and prevents paging through stale results", async () => {
  let finish: (response: Response) => void = () => {};
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...dashboard, assets: [asset], asset_total: 201 }))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText(asset.name);
  const button = screen.getByRole("button", { name: "Sort by CTR" });
  await userEvent.click(button);
  expect(document.activeElement).toBe(button);
  expect(
    screen.getByText("Updating products and ads…").closest("section")?.getAttribute("aria-busy"),
  ).toBe("true");
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  finish(Response.json({ ...dashboard, assets: [asset], asset_total: 201 }));
  await waitFor(() => expect(screen.queryByText("Updating products and ads…")).toBeNull());
  expect(document.activeElement).toBe(button);
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(false);
});

it("clears a loaded report when membership is removed during sorting", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...dashboard, assets: [asset], asset_total: 1 }))
    .mockResolvedValueOnce(new Response(null, { status: 404 }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <Portal
      section="assets"
      identity={identity}
      initialAccounts={{ items: [account], total: 1 }}
    />,
  );
  await screen.findByText(asset.name);
  await userEvent.click(screen.getByRole("button", { name: "Sort by CTR" }));
  await screen.findByText("This partner account is no longer available to you.");
  expect(screen.queryByText(asset.name)).toBeNull();
  expect(screen.queryByRole("table")).toBeNull();
});

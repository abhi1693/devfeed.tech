// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Portal } from "../src/app/portal";
import type { WorkspaceDashboard } from "../src/lib/api/generated/models";
import { partnerAccount, partnerIdentity } from "./partner-fixtures";

const navigation = vi.hoisted(() => ({ assign: vi.fn() }));
vi.mock("@devfeed/http/sso", async (load) => {
  const actual = await load<typeof import("@devfeed/http/sso")>();
  return {
    ...actual,
    navigateToSSOLogout: (value: unknown) => actual.navigateToSSOLogout(value, navigation.assign),
  };
});
const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const alpha = partnerAccount({ name: "Alpha" });
const beta = partnerAccount({ id: "22222222-2222-4222-8222-222222222222", name: "Beta" });
const identity = partnerIdentity();
const asset = {
  id: "33333333-3333-4333-8333-333333333333",
  product_id: "44444444-4444-4444-8444-444444444444",
  account_id: alpha.id,
  account_name: alpha.name,
  name: "API tool",
  kind: "product" as const,
  status: "active" as const,
  measured_days: 1,
  impressions: 100,
  clicks: 5,
  ctr: 5,
};
const dashboard: WorkspaceDashboard = {
  account_total: 205,
  start: "2026-10-01",
  end: "2026-10-07",
  totals: { impressions: 3000, clicks: 126, ctr: 4.2, measured_days: 2 },
  assets: [],
  asset_total: 0,
  trend: [],
  last_updated_at: null,
};
function portal(props: Partial<React.ComponentProps<typeof Portal>> = {}) {
  return render(
    <Portal identity={identity} initialAccounts={{ items: [alpha], total: 205 }} {...props} />,
  );
}
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows all authorized account totals without a selector or account-page fanout", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json(dashboard));
  vi.stubGlobal("fetch", fetcher);
  portal();
  await screen.findByText("3,000");
  expect(screen.getByText("4.20%")).toBeTruthy();
  expect(screen.getByText("205 partner accounts")).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save click destination" })).toBeNull();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/partner/dashboard?days=30&limit=25&offset=0&sort=name&order=asc",
    expect.anything(),
  );
  expect(window.location.pathname).toBe("/");
  expect(new URLSearchParams(window.location.search).has("account_id")).toBe(false);
  expect(screen.getByRole("link", { name: "Products" }).getAttribute("href")).toBe(
    "/products?days=30&sort=name&order=asc",
  );
});

it("shows account columns and distinct policy links for a product delivered by two accounts", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Response.json({
        ...dashboard,
        assets: [asset, { ...asset, account_id: beta.id, account_name: beta.name }],
        asset_total: 2,
      }),
    ),
  );
  portal({ section: "performance" });
  const table = await screen.findByRole("table", { name: "Product and ad results" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  for (const [index, account] of [alpha, beta].entries()) {
    const product = within(rows[index]).getByRole("link", { name: asset.name });
    expect(
      new URL(product.getAttribute("href")!, "https://partner.example").searchParams.get(
        "account_id",
      ),
    ).toBe(account.id);
    expect(within(rows[index]).getByRole("link", { name: account.name }).getAttribute("href")).toBe(
      `/settings/${account.id}`,
    );
  }
  expect(screen.queryByRole("button", { name: "Placements" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
});

it("shows visual format totals independently of grouped product results", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Response.json({
        ...dashboard,
        format_totals: [
          { ad_format: "feed", impressions: 90, clicks: 3, ctr: 3.33, measured_days: 1 },
          {
            ad_format: "header_sponsorship",
            impressions: 10,
            clicks: 2,
            ctr: 20,
            measured_days: 1,
          },
        ],
      }),
    ),
  );
  portal({ section: "performance" });
  const formats = await screen.findByRole("region", { name: "Format comparison" });
  expect(within(formats).getByText("Header sponsorship")).toBeTruthy();
  expect(within(formats).getByText(/20.00% CTR/)).toBeTruthy();
  expect(within(formats).getAllByRole("listitem")).toHaveLength(2);
});

it("distinguishes missing measurements from measured zero", async () => {
  const fetcher = vi.fn().mockImplementation(() =>
    Response.json({
      ...dashboard,
      totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 0 },
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "performance" });
  await screen.findByText(/0 of 7 days have measurements/);
  expect(within(screen.getByRole("region", { name: "Impressions" })).getByText("—")).toBeTruthy();
  fetcher.mockImplementation(() =>
    Response.json({
      ...dashboard,
      totals: { impressions: 0, clicks: 0, ctr: null, measured_days: 1 },
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText(/1 of 7 days have measurements/);
  expect(within(screen.getByRole("region", { name: "Impressions" })).getByText("0")).toBeTruthy();
});

it("keeps an explicit account filter local to the report while global navigation remains unfiltered", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Response.json({ ...dashboard, account_total: 1 }));
  vi.stubGlobal("fetch", fetcher);
  portal({ initialAccount: beta });
  await screen.findByText("3,000");
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/dashboard?days=30&limit=25&offset=0&sort=name&order=asc&account_id=${beta.id}`,
    expect.anything(),
  );
  expect(screen.getByText(/Filtered to Beta/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "Show all partner accounts" }).getAttribute("href")).toBe(
    "/?days=30&sort=name&order=asc",
  );
  expect(
    new URL(
      screen.getByRole("link", { name: "View performance" }).getAttribute("href")!,
      "https://partner.example",
    ).searchParams.get("account_id"),
  ).toBe(beta.id);
  expect(screen.getByRole("link", { name: "Products" }).getAttribute("href")).not.toContain(
    "account_id",
  );
});

it("sorts account and product names ascending by default and metrics descending", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Response.json({ ...dashboard, assets: [asset], asset_total: 1 }));
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "performance" });
  await screen.findByText(asset.name);
  for (const [field, label, expected] of [
    ["account", "Partner account", "asc"],
    ["impressions", "Impressions", "desc"],
    ["clicks", "Clicks", "desc"],
    ["ctr", "CTR", "desc"],
    ["name", "Name", "asc"],
  ]) {
    const button = screen.getByRole("button", { name: `Sort by ${label}` });
    await userEvent.click(button);
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith(
        `/api/v1/partner/dashboard?days=30&limit=25&offset=0&sort=${field}&order=${expected}`,
        expect.anything(),
      ),
    );
    expect(document.activeElement).toBe(button);
    await userEvent.click(button);
    await waitFor(() =>
      expect(fetcher).toHaveBeenLastCalledWith(
        `/api/v1/partner/dashboard?days=30&limit=25&offset=0&sort=${field}&order=${expected === "asc" ? "desc" : "asc"}`,
        expect.anything(),
      ),
    );
  }
});

it("restores global pagination and resets it when sort or reporting period changes", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Response.json({ ...dashboard, assets: [asset], asset_total: 205 }));
  vi.stubGlobal("fetch", fetcher);
  portal({
    section: "performance",
    initialDays: 7,
    initialSort: "ctr",
    initialOrder: "desc",
    initialAssetOffset: 25,
  });
  await screen.findByText(asset.name);
  expect(fetcher).toHaveBeenLastCalledWith(
    "/api/v1/partner/dashboard?days=7&limit=25&offset=25&sort=ctr&order=desc",
    expect.anything(),
  );
  expect(window.location.pathname).toBe("/performance");
  expect(new URLSearchParams(window.location.search).get("asset_offset")).toBe("25");
  await userEvent.click(screen.getByRole("button", { name: "Sort by Impressions" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/v1/partner/dashboard?days=7&limit=25&offset=0&sort=impressions&order=desc",
      expect.anything(),
    ),
  );
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText(asset.name);
  expect(fetcher).toHaveBeenLastCalledWith(
    "/api/v1/partner/dashboard?days=7&limit=25&offset=25&sort=impressions&order=desc",
    expect.anything(),
  );
  await userEvent.click(screen.getByRole("combobox", { name: "Reporting period" }));
  await userEvent.click(screen.getByRole("option", { name: "Last 30 days" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/v1/partner/dashboard?days=30&limit=25&offset=0&sort=impressions&order=desc",
      expect.anything(),
    ),
  );
});

it("recovers a saved report page when available inventory shrinks", async () => {
  const fetcher = vi.fn().mockImplementation((url: string) =>
    Response.json({
      ...dashboard,
      assets: url.includes("offset=25") ? [] : [asset],
      asset_total: 1,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "performance", initialAssetOffset: 25 });
  await screen.findByText(asset.name);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(new URLSearchParams(window.location.search).has("asset_offset")).toBe(false);
});

it("preserves sorting focus and disables paging while the next results are pending", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ ...dashboard, assets: [asset], asset_total: 205 }))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "performance" });
  await screen.findByText(asset.name);
  const button = screen.getByRole("button", { name: "Sort by CTR" });
  await userEvent.click(button);
  expect(document.activeElement).toBe(button);
  expect((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled).toBe(true);
  finish(Response.json({ ...dashboard, assets: [asset], asset_total: 205 }));
  await waitFor(() => expect(screen.queryByText("Updating products and ads…")).toBeNull());
  expect(document.activeElement).toBe(button);
});

it.each([401, 403])("clears report data when session access is lost (%s)", async (status) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(Response.json({ ...dashboard, assets: [asset], asset_total: 1 }))
      .mockResolvedValueOnce(new Response(null, { status })),
  );
  portal({ section: "performance" });
  await screen.findByText(asset.name);
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  expect(screen.queryByText(asset.name)).toBeNull();
});

it("rejects malformed workspace responses without exposing validation internals", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...dashboard, totals: null })));
  portal();
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("Could not confirm workspace performance"),
  );
  expect(screen.queryByText("3,000")).toBeNull();
});

it.each(["overview", "performance"] as const)(
  "explains portal access without memberships on %s",
  (section) => {
    vi.stubGlobal("fetch", vi.fn());
    portal({ section, initialAccounts: { items: [], total: 0 } });
    expect(screen.getByText(/no active partner membership/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it("renders a global settings list instead of mounting account forms", () => {
  vi.stubGlobal("fetch", vi.fn());
  portal({ section: "settings", initialAccounts: { items: [alpha, beta], total: 205 } });
  const table = screen.getByRole("table", { name: "Partner accounts" });
  expect(within(table).getByRole("link", { name: "Beta" }).getAttribute("href")).toBe(
    `/settings/${beta.id}`,
  );
  expect(screen.queryByRole("button", { name: "Save click destination" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("opens general account settings independently of the current account list without loading forms", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "settings", initialAccount: beta });
  expect(screen.getByRole("heading", { name: "Beta settings", level: 1 })).toBeTruthy();
  const navigation = screen.getByRole("navigation", { name: "Account settings sections" });
  expect(
    within(navigation).getByRole("link", { name: "General" }).getAttribute("aria-current"),
  ).toBe("page");
  expect(screen.queryByRole("button", { name: "Save click destination" })).toBeNull();
  expect(screen.queryByRole("form")).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});

it("opens only the requested account settings editor without loading workspace performance", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ product: null }));
  vi.stubGlobal("fetch", fetcher);
  portal({ section: "settings", settingsSection: "destination", initialAccount: beta });
  expect(screen.getByRole("heading", { name: "Beta settings", level: 1 })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Save click destination" })).toBeTruthy();
  await screen.findByText(/A preview will appear/);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${beta.id}/destination-preview`,
    expect.anything(),
  );
});

it("keeps sign-out errors retryable and sends the session CSRF token", async () => {
  let failed = true;
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      url.endsWith("/logout")
        ? Response.json(
            { url: "https://identity.example/end-session?state=partner" },
            { status: failed ? 503 : 200 },
          )
        : Response.json(dashboard),
    );
  vi.stubGlobal("fetch", fetcher);
  portal();
  await screen.findByText("3,000");
  await userEvent.click(screen.getByRole("button", { name: "User menu: Alice" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await screen.findByText("Could not sign out. Please try again.");
  expect(navigation.assign).not.toHaveBeenCalled();
  failed = false;
  await userEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  await waitFor(() =>
    expect(navigation.assign).toHaveBeenCalledWith(
      "https://identity.example/end-session?state=partner",
    ),
  );
  expect(fetcher).toHaveBeenCalledWith("/api/v1/partner/auth/sso/logout", {
    method: "POST",
    headers: { "x-csrf-token": "csrf" },
  });
});

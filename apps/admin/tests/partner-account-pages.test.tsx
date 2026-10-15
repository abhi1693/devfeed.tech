// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAdmin } from "./render-admin";
import { PartnerAccounts } from "@/components/organisms/partner-accounts";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";
import { PartnerAccountManagement } from "@/components/organisms/partner-account-management";
import { PartnerConnectionPage } from "@/components/organisms/partner-connection-page";
import { PartnerRunDetail } from "@/components/organisms/partner-run-detail";
import type { PartnerAccountRoute } from "@/lib/partner-account-routes";
import * as api from "@/lib/api/generated/admin";
import * as client from "@/lib/api/client";
import { ApiError } from "@/lib/api/client";
import type {
  ConnectionOut,
  PartnerPipelineOut,
  PartnerEvaluationDetail,
  AccountOut,
  CampaignOut,
  AssetOut,
  Dashboard,
} from "@/lib/api/generated/models";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/use-refresh-interval", () => ({ useRefreshInterval: () => 0 }));
vi.mock("@/components/organisms/job-logs", () => ({ JobLogs: () => <p>Run logs</p> }));
vi.mock("@/lib/api/generated/admin", () => ({
  adminPartnerToolsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerDestinationPreview: vi.fn().mockResolvedValue({ product: null }),
  adminPartnerAudienceTopics: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerAudienceTopicsCatalog: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerCreativePreview: vi.fn().mockResolvedValue({ product: null }),
  adminPartnerSponsorArticles: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerCampaignGet: vi.fn(),
  adminPartnerAssetGet: vi.fn(),
  adminPartnerCampaignUpdate: vi.fn(),
  adminPartnerCampaignCreate: vi.fn(),
  adminPartnerCampaignsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerInventoryList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerProductPromotionGet: vi.fn(),
  adminPartnerProductPromotionUpdate: vi.fn(),
  adminPartnerConnectionsList: vi.fn(),
  adminPartnerConnectionGet: vi.fn(),
  adminPartnerProvidersList: vi.fn().mockResolvedValue([]),
  adminPartnerAccountGet: vi.fn(),
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet: vi.fn(),
  membersV1AdminPartnerAccountsAccountIdMembersGet: vi.fn(),
  removeMemberV1AdminPartnerAccountsAccountIdMembersSubjectDelete: vi.fn(),
  adminPartnerConnectionAction: vi.fn(),
  adminPartnerPipelineGet: vi.fn(),
  adminPartnerEvaluationGet: vi.fn(),
  adminPartnerPipelineList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerEvaluationsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
const account: AccountOut & { members: number } = {
  id: "account-1",
  name: "Example partner",
  tier: "bronze" as const,
  status: "active" as const,
  benefits: ["Partner access"],
  allowed_promotion_kinds: ["product"],
  effective_promotion_kind: "product" as const,
  promotion_kind_source: "tier" as const,
  members: 1,
  advanced_targeting: false,
  allowed_ad_formats: ["feed"],
};
const asset = {
  id: "asset-1",
  name: "Example placement",
  kind: "ad" as const,
  status: "draft" as const,
  product_id: null,
  measured_days: 1,
  impressions: 1,
  clicks: 1,
  ctr: 1,
};
const assetRecord: AssetOut = {
  id: asset.id,
  name: asset.name,
  kind: asset.kind,
  status: asset.status,
  product_id: asset.product_id,
};
const dashboard = {
  account,
  assets: [asset],
  asset_total: 1,
  start: "2026-10-01",
  end: "2026-10-07",
  totals: { impressions: 1, clicks: 1, ctr: 1, measured_days: 1 },
  trend: [],
  last_updated_at: null,
};
const members = [
  {
    subject: "alice/a",
    issuer: "https://identity.example",
    user_id: "user-1",
    name: "Alice",
    email: "alice@example.test",
  },
];
const connection = {
  provider: "example",
  name: "Example connection",
  enabled: true,
  state: "idle",
  account_id: account.id,
  revision: 1,
  sync_interval_minutes: 60,
  products: 0,
  qualified: 0,
  checking: 0,
  needs_attention: 0,
  excluded: 0,
  ai_enabled: true,
  partnership_type: "launch_platform",
  api_url: "https://example.test/api",
  last_sync_at: null,
  next_sync_at: null,
  error: null,
} as ConnectionOut;
const fetcher = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.adminPartnerAccountGet).mockResolvedValue(account);
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue(dashboard);
  vi.mocked(api.membersV1AdminPartnerAccountsAccountIdMembersGet).mockResolvedValue(members);
  vi.mocked(
    api.removeMemberV1AdminPartnerAccountsAccountIdMembersSubjectDelete,
  ).mockResolvedValue();
  vi.mocked(api.adminPartnerAssetGet).mockImplementation(async (_accountId, assetId) => {
    if (assetId === assetRecord.id) return assetRecord;
    throw new ApiError(404);
  });
  vi.mocked(api.adminPartnerProvidersList).mockResolvedValue([]);
  vi.mocked(api.adminPartnerCampaignsList).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({ items: [], total: 0 });
  push.mockReset();
  fetcher.mockReset();
  vi.stubGlobal("fetch", fetcher);
  fetcher.mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("/tiers"))
      return Response.json([{ tier: "bronze", benefits: ["Partner access"] }]);
    if (path.includes("/dashboard")) return Response.json(dashboard);
    if (path.includes("/members"))
      return Response.json([
        {
          subject: "alice/a",
          issuer: "https://identity.example",
          user_id: "user-1",
          name: "Alice",
          email: "alice@example.test",
        },
      ]);
    return Response.json({ items: [account], total: 1 });
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it.each([
  { resource: "account", action: "view", report: true, membership: false },
  { resource: "performance", action: "list", report: true, membership: false },
  { resource: "account", action: "edit", report: false, membership: false },
  { resource: "product", action: "list", report: false, membership: false },
  { resource: "campaign", action: "list", report: false, membership: false },
  { resource: "member", action: "list", report: false, membership: true },
] as const)(
  "loads only the data needed for $resource/$action",
  async ({ resource, action, report, membership }) => {
    const route = { resource, action, accountId: account.id } as PartnerAccountRoute;
    renderAdmin(<PartnerAccountPage route={route} />);
    await screen.findByRole("heading", { name: account.name });
    await waitFor(() => expect(api.adminPartnerAccountGet).toHaveBeenCalledOnce());
    expect(api.adminPartnerAccountGet).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    if (report)
      await waitFor(() =>
        expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).toHaveBeenCalledOnce(),
      );
    else expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
    if (membership)
      await waitFor(() =>
        expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).toHaveBeenCalledOnce(),
      );
    else expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).not.toHaveBeenCalled();
  },
);

it("shows one report loading state, preserves navigation on failure and retries without a false empty table", async () => {
  let rejectReport!: (reason: Error) => void;
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet)
    .mockReturnValueOnce(
      new Promise<Dashboard>((_resolve, reject) => {
        rejectReport = reject;
      }),
    )
    .mockResolvedValueOnce(dashboard);
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "performance", action: "list", accountId: account.id }}
    />,
  );
  await screen.findByRole("heading", { name: account.name });
  expect(
    screen.getAllByRole("status").filter((node) => node.textContent === "Loading…"),
  ).toHaveLength(1);
  expect(screen.queryByRole("table", { name: "Placement reporting records" })).toBeNull();
  expect(screen.queryByText(/No placement reporting records yet/)).toBeNull();
  expect(screen.queryByText("0 records")).toBeNull();
  await act(async () => rejectReport(new ApiError(503)));
  await screen.findByText("The admin service is unavailable. Try again.");
  expect(screen.getByRole("navigation", { name: "Account sections" })).toBeTruthy();
  expect(screen.queryByRole("table", { name: "Placement reporting records" })).toBeNull();
  expect(screen.queryByText(/No placement reporting records yet/)).toBeNull();
  expect(screen.queryByText("0 records")).toBeNull();
  expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe(
    `/partnerships/accounts/${account.id}/edit`,
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("link", { name: `Edit ${asset.name}` });
  expect(screen.getByRole("table", { name: "Placement reporting records" })).toBeTruthy();
  expect(screen.queryByText("The admin service is unavailable. Try again.")).toBeNull();
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).toHaveBeenCalledTimes(2);
  expect(api.adminPartnerAccountGet).toHaveBeenCalledOnce();
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).not.toHaveBeenCalled();
});

it("isolates a membership failure and retries without fetching performance or reloading the account", async () => {
  vi.mocked(api.membersV1AdminPartnerAccountsAccountIdMembersGet)
    .mockRejectedValueOnce(new ApiError(503))
    .mockResolvedValueOnce(members);
  renderAdmin(
    <PartnerAccountPage route={{ resource: "member", action: "list", accountId: account.id }} />,
  );
  await screen.findByRole("heading", { name: account.name });
  await screen.findByText("The admin service is unavailable. Try again.");
  expect(screen.getByRole("link", { name: "Overview" }).getAttribute("href")).toBe(
    `/partnerships/accounts/${account.id}`,
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("link", { name: "Alice" });
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).toHaveBeenCalledTimes(2);
  expect(api.adminPartnerAccountGet).toHaveBeenCalledOnce();
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
});

it("lists accounts with detail and edit links and recovers from a failed request", async () => {
  fetcher.mockResolvedValueOnce(new Response(null, { status: 503 }));
  renderAdmin(<PartnerAccounts />);
  await screen.findByText("Could not load partner accounts.");
  fireEvent.click(screen.getByRole("button", { name: /retry/i }));
  expect((await screen.findByRole("link", { name: "Example partner" })).getAttribute("href")).toBe(
    "/partnerships/accounts/account-1",
  );
  expect(screen.getByRole("link", { name: "Edit Example partner" }).getAttribute("href")).toContain(
    "/edit",
  );
});

it("loads memberships only on the Members page and removes an encoded subject with CSRF", async () => {
  const view = renderAdmin(
    <PartnerAccountPage route={{ resource: "account", action: "view", accountId: account.id }} />,
  );
  await screen.findByRole("heading", { name: account.name });
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).not.toHaveBeenCalled();
  view.unmount();
  renderAdmin(
    <PartnerAccountPage route={{ resource: "member", action: "list", accountId: account.id }} />,
  );
  await screen.findByRole("link", { name: "Alice" });
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).toHaveBeenCalledWith(
    account.id,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(screen.queryByRole("table", { name: "Placement reporting records" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove member alice/a" }));
  await waitFor(() =>
    expect(
      api.removeMemberV1AdminPartnerAccountsAccountIdMembersSubjectDelete,
    ).toHaveBeenCalledWith(
      account.id,
      "alice%2Fa",
      expect.objectContaining({ headers: { "x-csrf-token": "test-csrf" } }),
    ),
  );
});

it("shows all formats for a grouped product rather than its compatibility scalar", async () => {
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue({
    ...dashboard,
    assets: [
      { ...asset, ad_format: "feed", ad_formats: ["engagement", "feed", "homepage_sponsorship"] },
    ],
  });
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "performance", action: "list", accountId: account.id }}
    />,
  );
  expect(await screen.findByText("Engagement · Feed · Homepage sponsorship")).toBeTruthy();
  expect(screen.queryByRole("columnheader", { name: "Type" })).toBeNull();
});

it("links aggregated totals to account policy without restricting the real asset's edit page", async () => {
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue({
    ...dashboard,
    assets: [{ ...asset, automatic: false, aggregated: true }],
  });
  const view = renderAdmin(
    <PartnerAccountPage
      route={{ resource: "performance", action: "list", accountId: account.id }}
    />,
  );
  await screen.findByText("Product totals");
  expect(
    screen.getByRole("link", { name: `View policy for ${asset.name}` }).getAttribute("href"),
  ).toBe(`/partnerships/accounts/${account.id}/edit`);
  expect(screen.queryByRole("link", { name: `Edit ${asset.name}` })).toBeNull();
  view.unmount();
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  expect(((await screen.findByLabelText(/Asset name/)) as HTMLInputElement).value).toBe(
    assetRecord.name,
  );
  expect(screen.getByRole("form", { name: "Asset settings" })).toBeTruthy();
});

it.each([404, 422])("fails closed for an unavailable asset (%s)", async (status) => {
  vi.mocked(api.adminPartnerAssetGet).mockRejectedValue(new ApiError(status));
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: "other" }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("This asset is unavailable");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it.each([
  { assets: [], asset_total: 150, offset: 125 },
  {
    assets: [{ ...asset, id: "other", name: "Another reporting record" }],
    asset_total: 150,
    offset: 50,
  },
])("loads asset details independently of the reporting page at offset $offset", async (page) => {
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
      initialOffset={page.offset}
      initialLimit={25}
    />,
  );
  expect(((await screen.findByLabelText(/Asset name/)) as HTMLInputElement).value).toBe(
    assetRecord.name,
  );
  expect(api.adminPartnerAssetGet).toHaveBeenCalledExactlyOnceWith(
    account.id,
    asset.id,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).not.toHaveBeenCalled();
});

it("does not refetch an asset when report pagination parameters change", async () => {
  const route = {
    resource: "asset" as const,
    action: "edit" as const,
    accountId: account.id,
    identifier: asset.id,
  };
  const view = renderAdmin(
    <PartnerAccountPage route={route} initialOffset={100} initialLimit={25} />,
  );
  await screen.findByRole("form", { name: "Asset settings" });
  view.rerender(<PartnerAccountPage route={route} initialOffset={250} initialLimit={50} />);
  expect(screen.getByRole("form", { name: "Asset settings" })).toBeTruthy();
  expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(1);
});

it("keeps the edit form hidden while asset details are loading", async () => {
  let resolve!: (value: AssetOut) => void;
  vi.mocked(api.adminPartnerAssetGet).mockReturnValue(
    new Promise<AssetOut>((done) => {
      resolve = done;
    }),
  );
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await waitFor(() => expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("status").textContent).toContain("Loading");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
  await act(async () => resolve(assetRecord));
  expect(await screen.findByRole("form", { name: "Asset settings" })).toBeTruthy();
});

it("retries failed asset requests without refetching the reporting page", async () => {
  vi.mocked(api.adminPartnerAssetGet)
    .mockRejectedValueOnce(new ApiError(503))
    .mockResolvedValueOnce(assetRecord);
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await screen.findByText("The admin service is unavailable. Try again.");
  expect(screen.queryByText(/This asset is unavailable/)).toBeNull();
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("form", { name: "Asset settings" });
  expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(2);
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
});

it("retries failed account details without refetching the loaded asset", async () => {
  vi.mocked(api.adminPartnerAccountGet)
    .mockRejectedValueOnce(new ApiError(503))
    .mockResolvedValueOnce(account);
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await screen.findByText("The admin service is unavailable. Try again.");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByRole("form", { name: "Asset settings" });
  expect(api.adminPartnerAccountGet).toHaveBeenCalledTimes(2);
  expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(1);
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
});

it("keeps automatic assets read-only even when they are absent from the reporting page", async () => {
  vi.mocked(api.adminPartnerAssetGet).mockResolvedValue({ ...assetRecord, automatic: true });
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("managed automatically");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it("redirects an expired asset-read session to sign-in", async () => {
  const login = vi.spyOn(client, "returnToLogin").mockImplementation(() => {});
  vi.mocked(api.adminPartnerAssetGet).mockRejectedValue(new ApiError(401));
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await waitFor(() => expect(login).toHaveBeenCalledOnce());
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
  expect(screen.queryByText(/This asset is unavailable/)).toBeNull();
});

it("aborts previous asset reads and ignores their late responses after navigation", async () => {
  const other = { ...assetRecord, id: "asset-2", name: "Other placement" };
  let resolveFirst!: (value: AssetOut) => void;
  let resolveOther!: (value: AssetOut) => void;
  vi.mocked(api.adminPartnerAssetGet).mockImplementation(
    (_accountId, assetId) =>
      new Promise<AssetOut>((resolve) => {
        if (assetId === asset.id) resolveFirst = resolve;
        else resolveOther = resolve;
      }),
  );
  const view = renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await waitFor(() => expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(1));
  const firstSignal = vi.mocked(api.adminPartnerAssetGet).mock.calls[0][2]?.signal;
  view.rerender(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: other.id }}
    />,
  );
  await waitFor(() => expect(api.adminPartnerAssetGet).toHaveBeenCalledTimes(2));
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => resolveFirst(assetRecord));
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
  expect(screen.queryByRole("heading", { name: `Edit ${assetRecord.name}` })).toBeNull();
  await act(async () => resolveOther(other));
  expect(((await screen.findByLabelText(/Asset name/)) as HTMLInputElement).value).toBe(other.name);
  view.unmount();
  const currentSignal = vi.mocked(api.adminPartnerAssetGet).mock.calls[1][2]?.signal;
  expect(currentSignal?.aborted).toBe(true);
});

it("waits for the routed account before mounting a newly loaded asset form", async () => {
  const otherAccount = { ...account, id: "account-2", name: "Other partner" };
  const otherAsset = { ...assetRecord, id: "asset-2", name: "Other partner asset" };
  let resolveAccount!: (value: AccountOut) => void;
  vi.mocked(api.adminPartnerAccountGet).mockImplementation(async (identifier) =>
    identifier === account.id
      ? account
      : new Promise<AccountOut>((resolve) => {
          resolveAccount = resolve;
        }),
  );
  vi.mocked(api.adminPartnerAssetGet).mockImplementation(async (identifier) =>
    identifier === account.id ? assetRecord : otherAsset,
  );
  const view = renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  await screen.findByRole("form", { name: "Asset settings" });
  view.rerender(
    <PartnerAccountPage
      route={{
        resource: "asset",
        action: "edit",
        accountId: otherAccount.id,
        identifier: otherAsset.id,
      }}
    />,
  );
  await waitFor(() =>
    expect(api.adminPartnerAssetGet).toHaveBeenCalledWith(
      otherAccount.id,
      otherAsset.id,
      expect.anything(),
    ),
  );
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
  await act(async () => resolveAccount(otherAccount));
  expect(((await screen.findByLabelText(/Asset name/)) as HTMLInputElement).value).toBe(
    otherAsset.name,
  );
  fireEvent.submit(screen.getByRole("form", { name: "Asset settings" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      `/api/v1/admin/partner-accounts/${otherAccount.id}/assets/${otherAsset.id}`,
      expect.objectContaining({ method: "PUT" }),
    ),
  );
});

it("loads an uppercase account UUID when the response returns its canonical lowercase ID", async () => {
  const requestedId = "B75DE541-8E35-4000-AA0D-4533F0A38110";
  const canonicalAccount = { ...account, id: requestedId.toLowerCase() };
  vi.mocked(api.adminPartnerAccountGet).mockResolvedValue(canonicalAccount);
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: requestedId, identifier: asset.id }}
    />,
  );
  expect(((await screen.findByLabelText(/Asset name/)) as HTMLInputElement).value).toBe(
    assetRecord.name,
  );
  expect(api.adminPartnerAssetGet).toHaveBeenCalledWith(requestedId, asset.id, expect.anything());
  fireEvent.submit(screen.getByRole("form", { name: "Asset settings" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      `/api/v1/admin/partner-accounts/${canonicalAccount.id}/assets/${asset.id}`,
      expect.objectContaining({ method: "PUT" }),
    ),
  );
});

it("keeps promotion controls in Delivery settings without the abandoned exposure limit", async () => {
  const user = userEvent.setup();
  const withIgnoredLimit = { ...account, frequency_cap: 20 };
  vi.mocked(api.adminPartnerAccountGet).mockResolvedValue(withIgnoredLimit);
  renderAdmin(
    <PartnerAccountPage route={{ resource: "account", action: "edit", accountId: account.id }} />,
  );
  await screen.findByRole("heading", { name: account.name });
  expect(screen.queryByText("Automatic product inclusion")).toBeNull();
  await user.click(screen.getByRole("tab", { name: "Delivery" }));
  expect(await screen.findByLabelText("Default promotion")).toBeTruthy();
  expect(screen.queryByText(/Product exposure limit|Frequency cap/i)).toBeNull();
  expect(screen.queryByText(/per reader installation per UTC day/i)).toBeNull();
});

it.each([
  {
    route: { resource: "account" as const, action: "edit" as const, accountId: account.id },
    title: account.name,
    form: "Partner account settings",
  },
  {
    route: { resource: "member" as const, action: "create" as const, accountId: account.id },
    title: "Add member",
    form: "Partner membership",
  },
  {
    route: { resource: "asset" as const, action: "create" as const, accountId: account.id },
    title: "Associate asset",
    form: "Asset settings",
  },
])("opens the declared $route.resource/$route.action form", async ({ route, title, form }) => {
  renderAdmin(<PartnerAccountPage route={route} />);
  await screen.findByRole("heading", { name: title });
  expect(screen.getByRole("form", { name: form })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Object sections" })).toBeNull();
  expect(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).not.toHaveBeenCalled();
  expect(api.membersV1AdminPartnerAccountsAccountIdMembersGet).not.toHaveBeenCalled();
});

it("opens an account creation form without fetching a nonexistent account", async () => {
  renderAdmin(<PartnerAccountPage route={{ resource: "account", action: "create" }} />);
  expect(await screen.findByRole("heading", { name: "Create partner account" })).toBeTruthy();
  expect(screen.getByRole("form", { name: "Partner account settings" })).toBeTruthy();
  expect(api.adminPartnerAccountGet).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/dashboard"))).toBe(false);
  expect(fetcher.mock.calls.some(([input]) => String(input).includes("/members"))).toBe(false);
});

it.each([
  {
    resource: "product" as const,
    region: "Account products",
    apiMethod: "adminPartnerInventoryList" as const,
  },
  {
    resource: "campaign" as const,
    region: "Paid campaigns",
    apiMethod: "adminPartnerCampaignsList" as const,
  },
])("opens the declared $resource list for the account", async ({ resource, region, apiMethod }) => {
  renderAdmin(<PartnerAccountPage route={{ resource, action: "list", accountId: account.id }} />);
  expect(await screen.findByRole("region", { name: region })).toBeTruthy();
  await waitFor(() =>
    expect(api[apiMethod]).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ offset: 0, limit: 25 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
  expect(
    screen
      .getByRole("link", { name: resource === "product" ? "Products" : "Campaigns" })
      .getAttribute("aria-current"),
  ).toBe("page");
});

it("selects and remounts asset edits by the declared identifier", async () => {
  const other = { ...asset, id: "asset-2", name: "Other placement" };
  vi.mocked(api.adminPartnerAssetGet).mockImplementation(async (_accountId, assetId) =>
    assetId === asset.id ? assetRecord : other,
  );
  const view = renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: asset.id }}
    />,
  );
  const name = await screen.findByLabelText(/Asset name/);
  expect((name as HTMLInputElement).value).toBe(asset.name);
  fireEvent.change(name, { target: { value: "Unsaved edit" } });
  view.rerender(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: other.id }}
    />,
  );
  expect(await screen.findByRole("heading", { name: `Edit ${other.name}` })).toBeTruthy();
  expect((screen.getByLabelText(/Asset name/) as HTMLInputElement).value).toBe(other.name);
});

it("rejects synthetic product reporting IDs instead of editing an aggregate", async () => {
  const aggregate = {
    ...asset,
    id: "product:123e4567-e89b-12d3-a456-426614174000",
    aggregated: true,
  };
  vi.mocked(api.adminPartnerAssetGet).mockRejectedValue(new ApiError(422));
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: aggregate.id }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("This asset is unavailable");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it("opens campaign creation without loading an edit identifier", async () => {
  vi.mocked(api.adminPartnerAccountGet).mockResolvedValue({
    ...account,
    allowed_promotion_kinds: ["product", "ad"],
  });
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "campaign", action: "create", accountId: account.id }}
    />,
  );
  expect(await screen.findByRole("form", { name: "Paid campaign settings" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Create paid campaign", level: 1 })).toBeTruthy();
  expect(api.adminPartnerCampaignGet).not.toHaveBeenCalled();
});

it("forwards campaign edit identity and discards previous unsaved fields on navigation", async () => {
  vi.mocked(api.adminPartnerAccountGet).mockResolvedValue({
    ...account,
    allowed_promotion_kinds: ["product", "ad"],
  });
  const campaign: CampaignOut = {
    account_id: account.id,
    id: "campaign:primary_1",
    name: "First campaign",
    starts_at: "2026-10-01T00:00:00Z",
    ends_at: "2026-10-31T00:00:00Z",
    status: "draft",
  };
  const other = { ...campaign, id: "campaign:primary_2", name: "Second campaign" };
  vi.mocked(api.adminPartnerCampaignGet).mockImplementation(async (_accountId, campaignId) =>
    campaignId === campaign.id ? campaign : other,
  );
  const view = renderAdmin(
    <PartnerAccountPage
      route={{
        resource: "campaign",
        action: "edit",
        accountId: account.id,
        identifier: campaign.id,
      }}
    />,
  );
  const name = await screen.findByLabelText(/Campaign name/);
  expect((name as HTMLInputElement).value).toBe(campaign.name);
  expect(api.adminPartnerCampaignGet).toHaveBeenCalledWith(
    account.id,
    campaign.id,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  fireEvent.change(name, { target: { value: "Unsaved campaign edit" } });
  view.rerender(
    <PartnerAccountPage
      route={{ resource: "campaign", action: "edit", accountId: account.id, identifier: other.id }}
    />,
  );
  await waitFor(() =>
    expect((screen.getByLabelText(/Campaign name/) as HTMLInputElement).value).toBe(other.name),
  );
  expect(api.adminPartnerCampaignGet).toHaveBeenCalledWith(
    account.id,
    other.id,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

it("creates an account with selected benefits and navigates to the saved record", async () => {
  fetcher.mockImplementation(async (input, init) =>
    init?.method === "POST"
      ? Response.json(account)
      : Response.json([{ tier: "bronze", benefits: ["Partner access"] }]),
  );
  renderAdmin(<PartnerAccountManagement mode="account" />);
  await screen.findByText("Partner access");
  fireEvent.change(screen.getByLabelText(/Partner name/), { target: { value: "Example partner" } });
  fireEvent.submit(screen.getByRole("form", { name: "Partner account settings" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1"));
  expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/admin/partner-accounts",
    expect.objectContaining({
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": "test-csrf" },
      body: JSON.stringify({
        name: "Example partner",
        tier: "bronze",
        status: "active",
        organic_enabled: true,
        auto_include_products: true,
        promotion_surfaces: ["article", "topic", "feed", "search"],
        promotion_kind: null,
        approval_mode: "qualified",
        outbound_destination: "product_url",
        audience: { experience_levels: [], language_topic_ids: [], tool_topic_ids: [] },
        ad_formats: ["feed"],
      }),
    }),
  );
});

it("retains edited account values on validation failure and allows cancellation", async () => {
  fetcher.mockImplementation(async (input, init) =>
    init?.method === "PUT"
      ? new Response(null, { status: 422 })
      : Response.json([{ tier: "bronze", benefits: ["Partner access"] }]),
  );
  renderAdmin(<PartnerAccountManagement mode="account" account={account} />);
  fireEvent.submit(screen.getByRole("form", { name: "Partner account settings" }));
  await screen.findByText("Check the form values and selected catalog product.");
  expect((screen.getByLabelText(/Partner name/) as HTMLInputElement).value).toBe("Example partner");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1");
});

it("loads a connection, links its account, and sends an authorized sync action", async () => {
  vi.mocked(api.adminPartnerConnectionGet).mockResolvedValue(connection);
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue(dashboard);
  vi.mocked(api.adminPartnerConnectionAction).mockResolvedValue({
    ...connection,
    state: "syncing",
  });
  renderAdmin(<PartnerConnectionPage provider="example" />);
  await screen.findByRole("heading", { name: "Example connection" });
  expect(api.adminPartnerConnectionGet).toHaveBeenCalledWith("example");
  expect(api.adminPartnerConnectionsList).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Import connections" }).getAttribute("href")).toBe(
    "/partnerships/connections",
  );
  expect(screen.getByRole("link", { name: "Example partner" }).getAttribute("href")).toContain(
    account.id,
  );
  fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionAction).toHaveBeenCalledWith(
      "example",
      { action: "sync" },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("reports missing connections rather than rendering settings for a different provider", async () => {
  vi.mocked(api.adminPartnerConnectionGet).mockRejectedValue(new ApiError(404));
  renderAdmin(<PartnerConnectionPage provider="missing" />);
  await screen.findByText("Import connection not found.");
  expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
});

it("checks each preset identity without loading the connections list when creating", async () => {
  vi.mocked(api.adminPartnerProvidersList).mockResolvedValue([
    {
      provider: "example",
      name: "Existing platform",
      partnership_type: "launch_platform",
      api_url: "https://example.test/api",
      description: "Existing preset",
    },
    {
      provider: "shipyardhq",
      name: "ShipyardHQ",
      partnership_type: "launch_platform",
      api_url: "https://shipyardhq.dev/api/products",
      description: "Available preset",
    },
  ]);
  vi.mocked(api.adminPartnerConnectionGet).mockImplementation(async (provider) => {
    if (provider === "example") return connection;
    throw new ApiError(404);
  });
  renderAdmin(<PartnerConnectionPage />);
  fireEvent.click(await screen.findByRole("combobox", { name: "Platform" }));
  expect(screen.queryByRole("option", { name: "Existing platform" })).toBeNull();
  expect(screen.getByRole("option", { name: "ShipyardHQ" })).toBeTruthy();
  expect(screen.getByRole("option", { name: "Custom API" })).toBeTruthy();
  expect(api.adminPartnerConnectionGet).toHaveBeenCalledWith("example");
  expect(api.adminPartnerConnectionGet).toHaveBeenCalledWith("shipyardhq");
  expect(api.adminPartnerConnectionsList).not.toHaveBeenCalled();
});

it.each([403, 503])("retains a failed preset access check as an error (%s)", async (status) => {
  vi.mocked(api.adminPartnerProvidersList).mockResolvedValue([
    {
      provider: "example",
      name: "Example",
      partnership_type: "launch_platform",
      api_url: "https://example.test/api",
      description: "Preset",
    },
  ]);
  vi.mocked(api.adminPartnerConnectionGet).mockRejectedValue(new ApiError(status));
  renderAdmin(<PartnerConnectionPage />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("form", { name: "Import connection settings" })).toBeNull();
});

it("shows a pipeline run's links, state and logs", async () => {
  vi.mocked(api.adminPartnerPipelineGet).mockResolvedValue({
    id: "run-12345678",
    provider: "example",
    operation: "sync",
    status: "succeeded",
    attempts: 1,
    max_attempts: 3,
    product_id: "product-1",
    product_name: "Example tool",
    parent_id: "parent-1",
    external_id: "external-1",
    created_at: "2026-10-01T00:00:00Z",
    available_at: "2026-10-01T00:00:00Z",
    finished_at: null,
    error: null,
  } as PartnerPipelineOut);
  renderAdmin(<PartnerRunDetail kind="pipeline" id="run-12345678" />);
  expect(await screen.findByRole("link", { name: "Example tool" })).toBeTruthy();
  expect(screen.getByText("Import connection")).toBeTruthy();
  expect(screen.getByRole("link", { name: "example" }).getAttribute("href")).toBe(
    "/partnerships/connections/example",
  );
  expect(screen.getByRole("link", { name: "parent-1" }).getAttribute("href")).toContain("parent-1");
  expect(screen.getByText("Run logs")).toBeTruthy();
});

it("shows direct product checks without fabricating an import connection", async () => {
  const job: PartnerPipelineOut = {
    id: "manual-run",
    provider: null,
    operation: "assess",
    status: "queued",
    attempts: 0,
    product_id: "product-1",
    product_name: "Direct tool",
    parent_id: null,
    external_id: null,
    created_at: "2026-10-01T00:00:00Z",
    available_at: "2026-10-01T00:00:00Z",
    finished_at: null,
    error: null,
  };
  vi.mocked(api.adminPartnerPipelineGet).mockResolvedValue(job);
  const detail = renderAdmin(<PartnerRunDetail kind="pipeline" id={job.id} />);
  expect(await screen.findByText("Direct account product")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Direct tool" }).getAttribute("href")).toBe(
    "/partnerships/products/product-1",
  );
  expect(screen.queryByText("Import connection")).toBeNull();
  expect(
    screen
      .getAllByRole("link")
      .some((link) => link.getAttribute("href")?.includes("/connections/")),
  ).toBe(false);
  detail.unmount();
  renderAdmin(<PartnerRunDetail kind="pipeline" id={job.id} section="related" />);
  expect((await screen.findByRole("link", { name: "Direct tool" })).getAttribute("href")).toBe(
    "/partnerships/products/product-1",
  );
  expect(screen.queryByText("Import connection")).toBeNull();
});

it("shows outdated evaluation evidence and article results", async () => {
  vi.mocked(api.adminPartnerEvaluationGet).mockResolvedValue({
    id: "eval-1",
    product_id: "product-1",
    status: "succeeded",
    current: false,
    snapshot: {
      product: {
        name: "Example tool",
        revision: 1,
        evidence: [{ quote: "Supports APIs", url: "https://example.test" }],
      },
      articles: [{ id: "article-1", title: "API engineering" }],
    },
    result: {
      decisions: [
        {
          article_id: "article-1",
          evidence_index: 0,
          relevant: true,
          reason: "Matches API tools",
          technology: "API",
          article_quote: "API tooling",
        },
      ],
    },
  } as PartnerEvaluationDetail);
  renderAdmin(<PartnerRunDetail kind="evaluations" id="eval-1" section="results" />);
  await screen.findByText("These results are outdated because product or article data changed.");
  expect(screen.getByRole("link", { name: "API engineering" }).getAttribute("href")).toBe(
    "/content/articles/article-1",
  );
  expect(screen.getByText("Supports APIs")).toBeTruthy();
});

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerAccounts } from "@/components/organisms/partner-accounts";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";
import { PartnerAccountManagement } from "@/components/organisms/partner-account-management";
import { PartnerConnectionPage } from "@/components/organisms/partner-connection-page";
import { PartnerRunDetail } from "@/components/organisms/partner-run-detail";
import * as api from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import type {
  ConnectionOut,
  PartnerPipelineOut,
  PartnerEvaluationDetail,
  AccountOut,
  CampaignOut,
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
  adminPartnerCampaignUpdate: vi.fn(),
  adminPartnerCampaignCreate: vi.fn(),
  adminPartnerCampaignsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerInventoryList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerProductPromotionGet: vi.fn(),
  adminPartnerProductPromotionUpdate: vi.fn(),
  adminPartnerConnectionsList: vi.fn(),
  adminPartnerConnectionGet: vi.fn(),
  adminPartnerProvidersList: vi.fn().mockResolvedValue([]),
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet: vi.fn(),
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
  vi.unstubAllGlobals();
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

it("shows account details and related memberships and removes an encoded subject with CSRF", async () => {
  const view = renderAdmin(
    <PartnerAccountPage route={{ resource: "account", action: "view", accountId: account.id }} />,
  );
  await screen.findByRole("heading", { name: "Example partner" });
  expect(screen.getByText("Partner access")).toBeTruthy();
  view.unmount();
  renderAdmin(
    <PartnerAccountPage route={{ resource: "related", action: "list", accountId: account.id }} />,
  );
  await screen.findByRole("link", { name: "Alice" });
  expect(
    screen.getByRole("link", { name: "Edit Example placement" }).getAttribute("href"),
  ).toContain("assets/asset-1/edit");
  fireEvent.click(screen.getByRole("button", { name: "Remove member alice/a" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/admin/partner-accounts/account-1/members/alice%2Fa",
      expect.objectContaining({ method: "DELETE", headers: { "x-csrf-token": "test-csrf" } }),
    ),
  );
});

it("shows all formats for a grouped product rather than its compatibility scalar", async () => {
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({
          ...dashboard,
          assets: [
            {
              ...asset,
              ad_format: "feed",
              ad_formats: ["engagement", "feed", "homepage_sponsorship"],
            },
          ],
        })
      : Response.json([]),
  );
  renderAdmin(
    <PartnerAccountPage route={{ resource: "related", action: "list", accountId: account.id }} />,
  );
  expect(await screen.findByText("Engagement · Feed · Homepage sponsorship")).toBeTruthy();
  expect(screen.queryByRole("columnheader", { name: "Type" })).toBeNull();
});

it("keeps aggregated mixed-product totals read-only while linking to account policy", async () => {
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({ ...dashboard, assets: [{ ...asset, automatic: false, aggregated: true }] })
      : Response.json([]),
  );
  const view = renderAdmin(
    <PartnerAccountPage route={{ resource: "related", action: "list", accountId: account.id }} />,
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
  await screen.findByText(/This product total combines multiple reporting records/);
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it("fails closed when an asset edit URL is outside the account", async () => {
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: "other" }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("This asset is unavailable");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it("shows promotion defaults without displaying a legacy product exposure limit", async () => {
  fetcher.mockImplementation(async () =>
    Response.json({ ...dashboard, account: { ...account, frequency_cap: 20 } }),
  );
  renderAdmin(
    <PartnerAccountPage route={{ resource: "account", action: "view", accountId: account.id }} />,
  );
  await screen.findByRole("heading", { name: account.name });
  expect(screen.getByText("Promotion defaults")).toBeTruthy();
  expect(screen.getByText("Automatic product inclusion")).toBeTruthy();
  expect(screen.queryByText(/Product exposure limit|Frequency cap/i)).toBeNull();
  expect(screen.queryByText(/per reader installation per UTC day/i)).toBeNull();
});

it.each([
  {
    route: { resource: "account" as const, action: "edit" as const, accountId: account.id },
    title: `Edit ${account.name}`,
    form: "Partnership settings",
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
});

it("opens an account creation form without fetching a nonexistent account", async () => {
  renderAdmin(<PartnerAccountPage route={{ resource: "account", action: "create" }} />);
  expect(await screen.findByRole("heading", { name: "Create partner account" })).toBeTruthy();
  expect(screen.getByRole("form", { name: "Partnership settings" })).toBeTruthy();
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
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({ ...dashboard, assets: [asset, other], asset_total: 2 })
      : Response.json([]),
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

it("keeps synthetic product totals read-only when routed with their real identifier", async () => {
  const aggregate = {
    ...asset,
    id: "product:123e4567-e89b-12d3-a456-426614174000",
    aggregated: true,
  };
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({ ...dashboard, assets: [aggregate] })
      : Response.json([]),
  );
  renderAdmin(
    <PartnerAccountPage
      route={{ resource: "asset", action: "edit", accountId: account.id, identifier: aggregate.id }}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("cannot be edited as one asset");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
});

it("opens campaign creation without loading an edit identifier", async () => {
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({
          ...dashboard,
          account: { ...account, allowed_promotion_kinds: ["product", "ad"] },
        })
      : Response.json([]),
  );
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
  const campaign: CampaignOut = {
    account_id: account.id,
    id: "campaign:primary_1",
    name: "First campaign",
    starts_at: "2026-10-01T00:00:00Z",
    ends_at: "2026-10-31T00:00:00Z",
    status: "draft",
  };
  const other = { ...campaign, id: "campaign:primary_2", name: "Second campaign" };
  fetcher.mockImplementation(async (input) =>
    String(input).includes("/dashboard")
      ? Response.json({
          ...dashboard,
          account: { ...account, allowed_promotion_kinds: ["product", "ad"] },
        })
      : Response.json([]),
  );
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
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
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
        promotion_surfaces: ["article", "topic", "feed"],
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
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
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

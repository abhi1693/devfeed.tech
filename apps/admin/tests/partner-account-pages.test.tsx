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
import type {
  ConnectionOut,
  PartnerPipelineOut,
  PartnerEvaluationDetail,
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
  adminPartnerConnectionsList: vi.fn(),
  adminPartnerProvidersList: vi.fn().mockResolvedValue([]),
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet: vi.fn(),
  adminPartnerConnectionAction: vi.fn(),
  adminPartnerPipelineGet: vi.fn(),
  adminPartnerEvaluationGet: vi.fn(),
  adminPartnerPipelineList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerEvaluationsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
const account = {
  id: "account-1",
  name: "Example partner",
  tier: "bronze" as const,
  status: "active" as const,
  benefits: ["Partner access"],
  members: 1,
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
  const view = renderAdmin(<PartnerAccountPage id={account.id} />);
  await screen.findByRole("heading", { name: "Example partner" });
  expect(screen.getByText("Partner access")).toBeTruthy();
  view.unmount();
  renderAdmin(<PartnerAccountPage id={account.id} section="related" />);
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

it("fails closed when an asset edit URL is outside the account", async () => {
  renderAdmin(<PartnerAccountPage id={account.id} section="assets/other/edit" />);
  expect((await screen.findByRole("alert")).textContent).toContain("This asset is unavailable");
  expect(screen.queryByRole("form", { name: "Asset settings" })).toBeNull();
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
      body: JSON.stringify({ name: "Example partner", tier: "bronze", status: "active" }),
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
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue([connection]);
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue(dashboard);
  vi.mocked(api.adminPartnerConnectionAction).mockResolvedValue({
    ...connection,
    state: "syncing",
  });
  renderAdmin(<PartnerConnectionPage provider="example" />);
  await screen.findByRole("heading", { name: "Example connection" });
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
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue([]);
  renderAdmin(<PartnerConnectionPage provider="missing" />);
  await screen.findByText("Partner not found.");
  expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
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
  expect(screen.getByRole("link", { name: "parent-1" }).getAttribute("href")).toContain("parent-1");
  expect(screen.getByText("Run logs")).toBeTruthy();
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

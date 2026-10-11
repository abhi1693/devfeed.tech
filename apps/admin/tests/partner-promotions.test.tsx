// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerCampaignPage } from "@/components/organisms/partner-campaign-form";
import { PartnerCampaigns } from "@/components/organisms/partner-campaigns";
import { PartnerInventory } from "@/components/organisms/partner-inventory";
import { PartnerAccountManagement } from "@/components/organisms/partner-account-management";
import { ApiError } from "@/lib/api/client";
import * as api from "@/lib/api/generated/admin";
import type { AccountOut, CampaignOut } from "@/lib/api/generated/models";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api/generated/admin", () => ({
  adminPartnerCampaignsList: vi.fn(),
  adminPartnerCampaignGet: vi.fn(),
  adminPartnerCampaignCreate: vi.fn(),
  adminPartnerCampaignUpdate: vi.fn(),
  adminPartnerInventoryList: vi.fn(),
  adminPartnerProductPromotionUpdate: vi.fn(),
  adminPartnerToolsList: vi.fn(),
}));
const account: AccountOut = {
  id: "account-1",
  name: "Example partner",
  status: "active",
  tier: "bronze",
  benefits: [],
  organic_enabled: true,
  auto_include_products: true,
  promotion_surfaces: ["article", "feed"],
  frequency_cap: 3,
};
const product = {
  id: "product-1",
  name: "Kubernetes helper",
  technologies: ["Kubernetes"],
  status: "approved",
  excluded: false,
  globally_excluded: false,
};
const campaign: CampaignOut = {
  id: "campaign-1",
  account_id: account.id,
  name: "October promotion",
  status: "active",
  targeting: "all",
  product_ids: [],
  topic_ids: [],
  technologies: [],
  surfaces: ["article"],
  starts_at: "2026-10-01T00:00:00Z",
  ends_at: "2026-11-01T00:00:00Z",
  priority: 50,
  daily_impression_limit: 100,
  impressions: 200,
  clicks: 10,
  ctr: 0.05,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.adminPartnerCampaignsList).mockResolvedValue({ items: [campaign], total: 1 });
  vi.mocked(api.adminPartnerCampaignGet).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerCampaignCreate).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerCampaignUpdate).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({ items: [product], total: 1 });
  vi.mocked(api.adminPartnerProductPromotionUpdate).mockResolvedValue({
    account_id: account.id,
    product_id: product.id,
    excluded: true,
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fillDates() {
  fireEvent.change(screen.getByLabelText(/^Starts/), { target: { value: "2026-10-01T10:00" } });
  fireEvent.change(screen.getByLabelText(/^Ends/), { target: { value: "2026-11-01T10:00" } });
}

it("saves promotion defaults once on the account without requiring a product type", async () => {
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT"
      ? Response.json(account)
      : Response.json([{ tier: "bronze", benefits: [] }]),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={account} />);
  fireEvent.click(screen.getByLabelText(/Allow ordinary recommendations/));
  fireEvent.click(screen.getByLabelText(/Automatically include imported products/));
  fireEvent.click(screen.getByLabelText("Reader feeds"));
  fireEvent.change(screen.getByLabelText(/Product exposure limit/), { target: { value: "5" } });
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1"));
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    organic_enabled: false,
    auto_include_products: false,
    promotion_surfaces: ["article"],
    frequency_cap: 5,
  });
  expect(screen.queryByRole("combobox", { name: "Type" })).toBeNull();
});

it("creates a finite paid catalog campaign inheriting the account's allowed placements", async () => {
  renderAdmin(<PartnerCampaignPage account={account} />);
  fireEvent.change(screen.getByLabelText(/Campaign name/), {
    target: { value: "Catalog agreement" },
  });
  fillDates();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() => expect(api.adminPartnerCampaignCreate).toHaveBeenCalled());
  expect(api.adminPartnerCampaignCreate).toHaveBeenCalledWith(
    account.id,
    expect.objectContaining({
      name: "Catalog agreement",
      status: "draft",
      targeting: "all",
      product_ids: [],
      topic_ids: [],
      technologies: [],
      surfaces: ["article", "feed"],
      daily_impression_limit: null,
    }),
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  const sent = vi.mocked(api.adminPartnerCampaignCreate).mock.calls[0][1];
  expect(new Date(sent.ends_at).getTime()).toBeGreaterThan(new Date(sent.starts_at).getTime());
  expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1/campaigns");
});

it("selects products only from this account's inventory and rejects an empty selection", async () => {
  renderAdmin(<PartnerCampaignPage account={account} />);
  fireEvent.change(screen.getByLabelText(/Campaign name/), {
    target: { value: "Featured product" },
  });
  fillDates();
  fireEvent.click(screen.getByRole("combobox", { name: "Product targeting" }));
  fireEvent.click(screen.getByRole("option", { name: "Selected account products" }));
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("Select at least one product from this account.");
  expect(api.adminPartnerCampaignCreate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Add account product" }));
  fireEvent.click(await screen.findByRole("option", { name: /Kubernetes helper/ }));
  expect(screen.getByRole("combobox", { name: "Selected product 1" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Add account product" })).toBeTruthy();
  await waitFor(() =>
    expect(api.adminPartnerInventoryList).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ offset: 0, limit: 25 }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ),
  );
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() =>
    expect(api.adminPartnerCampaignCreate).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ targeting: "selected", product_ids: [product.id] }),
      expect.anything(),
    ),
  );
});

it("validates filters and dates before sending a campaign", async () => {
  renderAdmin(<PartnerCampaignPage account={account} />);
  fillDates();
  fireEvent.click(screen.getByRole("combobox", { name: "Product targeting" }));
  fireEvent.click(screen.getByRole("option", { name: "Products matching topics or technologies" }));
  expect(screen.getByRole("combobox", { name: "Add topic" })).toBeTruthy();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("Choose a topic or enter a technology for product targeting.");
  fireEvent.change(screen.getByLabelText(/^Technologies/), {
    target: { value: "Kubernetes, PostgreSQL" },
  });
  fireEvent.change(screen.getByLabelText(/^Ends/), { target: { value: "2026-09-01T10:00" } });
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("Choose an end date after the start date.");
  expect(api.adminPartnerCampaignCreate).not.toHaveBeenCalled();
  fillDates();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() =>
    expect(api.adminPartnerCampaignCreate).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ targeting: "filters", technologies: ["Kubernetes", "PostgreSQL"] }),
      expect.anything(),
    ),
  );
});

it("loads the exact scoped campaign and retains edited values when a save is denied", async () => {
  vi.mocked(api.adminPartnerCampaignUpdate).mockRejectedValue(new ApiError(403));
  renderAdmin(<PartnerCampaignPage account={account} campaignId={campaign.id} />);
  await screen.findByDisplayValue(campaign.name);
  expect(api.adminPartnerCampaignGet).toHaveBeenCalledWith(
    account.id,
    campaign.id,
    expect.anything(),
  );
  fireEvent.change(screen.getByLabelText(/Campaign name/), {
    target: { value: "Updated agreement" },
  });
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("You do not have access to this action.");
  expect(screen.getByDisplayValue("Updated agreement")).toBeTruthy();
  expect(push).not.toHaveBeenCalled();
});

it("fails closed for a campaign outside the account and permits retry", async () => {
  vi.mocked(api.adminPartnerCampaignGet).mockRejectedValueOnce(
    new ApiError(404, "Campaign not found."),
  );
  renderAdmin(<PartnerCampaignPage account={account} campaignId="foreign-campaign" />);
  await screen.findByText("Campaign not found.");
  expect(screen.queryByRole("form", { name: "Paid campaign settings" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByDisplayValue(campaign.name);
});

it("shows actionable server validation while retaining campaign values", async () => {
  vi.mocked(api.adminPartnerCampaignUpdate).mockRejectedValue(
    new ApiError(422, "Please correct the highlighted fields.", {
      daily_impression_limit: "Use a positive impression limit.",
    }),
  );
  renderAdmin(<PartnerCampaignPage account={account} campaignId={campaign.id} />);
  await screen.findByDisplayValue(campaign.name);
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("daily_impression_limit: Use a positive impression limit.");
  expect(screen.getByDisplayValue(campaign.name)).toBeTruthy();
  expect(push).not.toHaveBeenCalled();
});

it("shows campaign attribution totals and scoped edit links", async () => {
  renderAdmin(<PartnerCampaigns accountId={account.id} />);
  expect((await screen.findByRole("link", { name: campaign.name })).getAttribute("href")).toBe(
    "/partnerships/accounts/account-1/campaigns/campaign-1/edit",
  );
  expect(screen.getByText("5.00%")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Create paid campaign" }).getAttribute("href")).toContain(
    "campaigns/new",
  );
});

it("searches account products and excludes them with CSRF while preserving inherited defaults", async () => {
  renderAdmin(<PartnerInventory account={account} />);
  await screen.findByRole("link", { name: product.name });
  expect(screen.getByText("Inherits account defaults")).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox", { name: "Product name" }), {
    target: { value: "Kubernetes" },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Search account products" }));
  await waitFor(() =>
    expect(api.adminPartnerInventoryList).toHaveBeenCalledWith(
      account.id,
      expect.objectContaining({ q: "Kubernetes", offset: 0 }),
      expect.anything(),
    ),
  );
  fireEvent.click(await screen.findByRole("button", { name: `Exclude ${product.name}` }));
  await waitFor(() =>
    expect(api.adminPartnerProductPromotionUpdate).toHaveBeenCalledWith(
      account.id,
      product.id,
      { excluded: true },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("preserves global qualification blocks and explains disabled automatic inclusion", async () => {
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({
    items: [{ ...product, excluded: true, globally_excluded: true }],
    total: 1,
  });
  renderAdmin(<PartnerInventory account={{ ...account, auto_include_products: false }} />);
  await screen.findByText("Blocked by product checks");
  expect(
    (screen.getByRole("button", { name: `Allow ${product.name}` }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(screen.getByText(/Automatic inclusion is off/)).toBeTruthy();
  expect(api.adminPartnerProductPromotionUpdate).not.toHaveBeenCalled();
});

it("allows persistent account exclusion while product checks independently block delivery", async () => {
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({
    items: [{ ...product, globally_excluded: true }],
    total: 1,
  });
  renderAdmin(<PartnerInventory account={account} />);
  await screen.findByText("Blocked by product checks");
  const exclude = screen.getByRole("button", { name: `Exclude ${product.name}` });
  expect((exclude as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(exclude);
  await waitFor(() =>
    expect(api.adminPartnerProductPromotionUpdate).toHaveBeenCalledWith(
      account.id,
      product.id,
      { excluded: true },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

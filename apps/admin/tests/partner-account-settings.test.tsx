// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PartnerAccountManagement } from "@/components/organisms/partner-account-management";
import type { AccountOut, PartnershipTierOut } from "@/lib/api/generated/models";
import * as api from "@/lib/api/generated/admin";
import { renderAdmin } from "./render-admin";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api/generated/admin", () => ({
  adminPartnerToolsList: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerDestinationPreview: vi.fn().mockResolvedValue({ product: null }),
  adminPartnerAudienceTopics: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerAudienceTopicsCatalog: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  adminPartnerCreativePreview: vi.fn().mockResolvedValue({ product: null }),
  adminPartnerSponsorArticles: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));

const account: AccountOut = {
  id: "account-1",
  name: "Example partner",
  tier: "platinum",
  status: "active",
  benefits: ["Relevant sponsored placements", "Advanced audience targeting"],
  allowed_promotion_kinds: ["product", "ad"],
  effective_promotion_kind: "ad",
  promotion_kind_source: "tier",
  advanced_targeting: true,
  allowed_ad_formats: ["feed", "header_sponsorship", "engagement"],
  ad_formats: ["feed"],
  promotion_surfaces: ["article", "topic", "feed", "search"],
};
const tiers: PartnershipTierOut[] = [
  {
    tier: "platinum",
    benefits: account.benefits,
    default_promotion_kind: "ad",
    allowed_promotion_kinds: account.allowed_promotion_kinds,
    allowed_ad_formats: account.allowed_ad_formats,
    advanced_targeting: true,
  },
  {
    tier: "bronze",
    benefits: ["Relevant product recommendations"],
    default_promotion_kind: "product",
    allowed_promotion_kinds: ["product"],
    allowed_ad_formats: ["feed"],
    advanced_targeting: false,
  },
];
const fetcher = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  push.mockReset();
  fetcher.mockReset();
  fetcher.mockImplementation(async (_input, init) =>
    init?.method === "PUT" || init?.method === "POST"
      ? Response.json(account)
      : Response.json(tiers),
  );
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function selectOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  value: string,
) {
  await user.click(screen.getByRole("combobox", { name: label }));
  await user.click(screen.getByRole("option", { name: value }));
}

it("shows one settings panel and supports arrow, Home and End keyboard navigation", async () => {
  const user = userEvent.setup();
  renderAdmin(<PartnerAccountManagement account={account} mode="account" />);
  const tabs = screen.getByRole("tablist", { name: "Account settings" });
  expect(
    within(tabs)
      .getAllByRole("tab")
      .map((tab) => tab.textContent),
  ).toEqual(["General", "Delivery", "Ad formats", "Audience"]);
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  expect(screen.getByRole("tabpanel", { name: "General" })).toBeTruthy();
  await user.click(screen.getByRole("tab", { name: "General" }));
  await user.keyboard("{ArrowRight}");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Delivery" }));
  expect(screen.getByRole("tabpanel", { name: "Delivery" })).toBeTruthy();
  await user.keyboard("{End}");
  expect(screen.getByRole("tabpanel", { name: "Audience" })).toBeTruthy();
  await user.keyboard("{ArrowLeft}");
  expect(screen.getByRole("tabpanel", { name: "Ad formats" })).toBeTruthy();
  await user.keyboard("{Home}");
  expect(screen.getByRole("tabpanel", { name: "General" })).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Save partnership" })).toHaveLength(1);
});

it("loads preview and selected audience data only when their settings are first opened", async () => {
  const user = userEvent.setup();
  renderAdmin(
    <PartnerAccountManagement
      account={{
        ...account,
        audience: { experience_levels: [], language_topic_ids: ["language-1"], tool_topic_ids: [] },
      }}
      mode="account"
    />,
  );
  await screen.findByText("Included benefits");
  await waitFor(() => expect(screen.queryByText("Loading tier options…")).toBeNull());
  expect(api.adminPartnerDestinationPreview).not.toHaveBeenCalled();
  expect(api.adminPartnerCreativePreview).not.toHaveBeenCalled();
  expect(api.adminPartnerAudienceTopics).not.toHaveBeenCalled();
  expect(screen.queryByText("Creative preview")).toBeNull();

  await user.click(screen.getByRole("tab", { name: "Delivery" }));
  await waitFor(() => expect(api.adminPartnerDestinationPreview).toHaveBeenCalledTimes(1));
  expect(api.adminPartnerAudienceTopics).not.toHaveBeenCalled();
  await user.click(screen.getByRole("tab", { name: "Ad formats" }));
  await user.click(screen.getByText("Creative preview"));
  await waitFor(() => expect(api.adminPartnerCreativePreview).toHaveBeenCalledTimes(1));
  const preview = screen.getByText("Creative preview").closest("details")!;
  expect(preview.open).toBe(true);
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  await waitFor(() => expect(api.adminPartnerAudienceTopics).toHaveBeenCalledTimes(1));
  await user.click(screen.getByRole("tab", { name: "General" }));
  await user.click(screen.getByRole("tab", { name: "Delivery" }));
  await user.click(screen.getByRole("tab", { name: "Ad formats" }));
  expect(screen.getByText("Creative preview").closest("details")).toBe(preview);
  expect(preview.open).toBe(true);
  expect(api.adminPartnerDestinationPreview).toHaveBeenCalledTimes(1);
  expect(api.adminPartnerCreativePreview).toHaveBeenCalledTimes(1);
  expect(api.adminPartnerAudienceTopics).toHaveBeenCalledTimes(1);
});

it("loads an initial Audience tab without mounting unrelated previews", async () => {
  renderAdmin(
    <PartnerAccountManagement
      account={{
        ...account,
        audience: { experience_levels: [], language_topic_ids: ["language-1"], tool_topic_ids: [] },
      }}
      mode="account"
      initialTab="audience"
    />,
  );
  expect(screen.getByRole("tabpanel", { name: "Audience" })).toBeTruthy();
  await waitFor(() => expect(api.adminPartnerAudienceTopics).toHaveBeenCalledTimes(1));
  expect(api.adminPartnerDestinationPreview).not.toHaveBeenCalled();
  expect(api.adminPartnerCreativePreview).not.toHaveBeenCalled();
});

it("retains every tab's unsaved draft and submits them together with CSRF", async () => {
  const user = userEvent.setup();
  renderAdmin(<PartnerAccountManagement account={account} mode="account" />);
  const name = screen.getByRole("textbox", { name: /Partner name/ });
  await user.clear(name);
  await user.type(name, "Updated partner");
  await selectOption(user, "Status", "Paused");
  await user.click(screen.getByRole("tab", { name: "Delivery" }));
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Default promotion" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  await selectOption(user, "Default promotion", "Product recommendation");
  await selectOption(user, "Product acceptance", "Trust account products");
  await selectOption(user, "Product click destination", "Partner listing");
  await user.click(screen.getByRole("checkbox", { name: /Allow ordinary recommendations/ }));
  await user.click(
    screen.getByRole("checkbox", { name: /Automatically include account products/ }),
  );
  await user.click(screen.getByRole("checkbox", { name: "Search results" }));
  await user.click(screen.getByRole("tab", { name: "Ad formats" }));
  await user.click(screen.getByRole("checkbox", { name: "Engagement" }));
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  await user.click(screen.getByRole("checkbox", { name: "Senior" }));
  await user.click(screen.getByRole("tab", { name: "General" }));
  expect(screen.getByRole("textbox", { name: /Partner name/ })).toBe(name);
  expect((name as HTMLInputElement).value).toBe("Updated partner");
  expect(screen.getByRole("combobox", { name: "Status" }).textContent).toBe("Paused");
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  expect((screen.getByRole("checkbox", { name: "Senior" }) as HTMLInputElement).checked).toBe(true);
  await user.click(screen.getByRole("button", { name: "Save partnership" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1"));
  const writes = fetcher.mock.calls.filter(([, init]) => init?.method === "PUT");
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe("/api/v1/admin/partner-accounts/account-1");
  expect(writes[0][1].headers).toEqual({
    "content-type": "application/json",
    "x-csrf-token": "test-csrf",
  });
  expect(JSON.parse(writes[0][1].body)).toEqual({
    name: "Updated partner",
    tier: "platinum",
    status: "paused",
    organic_enabled: false,
    auto_include_products: false,
    promotion_surfaces: ["article", "topic", "feed"],
    promotion_kind: "product",
    approval_mode: "trusted",
    outbound_destination: "listing_url",
    audience: { experience_levels: ["senior"], language_topic_ids: [], tool_topic_ids: [] },
    ad_formats: ["feed", "engagement"],
  });
}, 15_000);

it("opens a requested section and keeps the current inherited promotion accurate while tiers load", () => {
  fetcher.mockReturnValue(new Promise(() => {}));
  renderAdmin(<PartnerAccountManagement account={account} mode="account" initialTab="delivery" />);
  expect(screen.getByRole("tabpanel", { name: "Delivery" })).toBeTruthy();
  expect(screen.getByText(/Effective: Sponsored · inherited from tier/)).toBeTruthy();
  expect(
    (screen.getByRole("checkbox", { name: /Allow ordinary recommendations/ }) as HTMLInputElement)
      .disabled,
  ).toBe(true);
});

it("reveals General and focuses an invalid name when saving from a different tab", async () => {
  const user = userEvent.setup();
  renderAdmin(<PartnerAccountManagement mode="account" initialTab="formats" />);
  await user.click(screen.getByRole("button", { name: "Create account" }));
  await waitFor(() => expect(screen.getByRole("tabpanel", { name: "General" })).toBeTruthy());
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: /Partner name/ })),
  );
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
});

it("returns to Delivery for empty placement selection without discarding the account name", async () => {
  const user = userEvent.setup();
  renderAdmin(<PartnerAccountManagement account={account} mode="account" />);
  await user.type(screen.getByRole("textbox", { name: /Partner name/ }), " draft");
  await user.click(screen.getByRole("tab", { name: "Delivery" }));
  for (const label of [
    "Article pages",
    "Topic and category pages",
    "Reader feeds",
    "Search results",
  ])
    await user.click(screen.getByRole("checkbox", { name: label }));
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  await user.click(screen.getByRole("button", { name: "Save partnership" }));
  expect(await screen.findByText("Select at least one placement surface.")).toBeTruthy();
  expect(screen.getByRole("tabpanel", { name: "Delivery" })).toBeTruthy();
  await user.click(screen.getByRole("tab", { name: "General" }));
  expect((screen.getByRole("textbox", { name: /Partner name/ }) as HTMLInputElement).value).toBe(
    "Example partner draft",
  );
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
});

it("retains restricted audience and format choices when a tier changes", async () => {
  const user = userEvent.setup();
  renderAdmin(<PartnerAccountManagement account={account} mode="account" initialTab="formats" />);
  await user.click(screen.getByRole("checkbox", { name: "Engagement" }));
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  await user.click(screen.getByRole("checkbox", { name: "Senior" }));
  await user.click(screen.getByRole("tab", { name: "General" }));
  await selectOption(user, "Partnership tier", "Bronze");
  await user.click(screen.getByRole("tab", { name: "Ad formats" }));
  const engagement = screen.getByRole("checkbox", { name: "Engagement" }) as HTMLInputElement;
  expect(engagement.checked).toBe(true);
  expect(engagement.disabled).toBe(true);
  await user.click(screen.getByRole("tab", { name: "Audience" }));
  expect(screen.getByText(/Saved restrictions are retained/)).toBeTruthy();
  expect(screen.getByText("Senior")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Save partnership" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(write![1].body)).toMatchObject({
    tier: "bronze",
    ad_formats: ["feed", "engagement"],
    audience: { experience_levels: ["senior"] },
  });
});

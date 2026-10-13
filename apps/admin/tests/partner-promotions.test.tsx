// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerCampaignPage } from "@/components/organisms/partner-campaign-form";
import { PartnerCampaigns } from "@/components/organisms/partner-campaigns";
import { PartnerInventory } from "@/components/organisms/partner-inventory";
import { PartnerAccountManagement } from "@/components/organisms/partner-account-management";
import { PartnerProductPlacements } from "@/components/organisms/partner-ad-formats";
import { ApiError } from "@/lib/api/client";
import * as client from "@/lib/api/client";
import * as api from "@/lib/api/generated/admin";
import type { AccountOut, ArticleOut, CampaignOut } from "@/lib/api/generated/models";

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
  adminPartnerDestinationPreview: vi.fn(),
  adminPartnerAudienceTopics: vi.fn(),
  adminPartnerAudienceTopicsCatalog: vi.fn(),
  adminPartnerCreativePreview: vi.fn(),
  adminPartnerSponsorArticles: vi.fn(),
  adminPartnerProductPromotionGet: vi.fn(),
  adminPartnerProductAdImageUpload: vi.fn(),
}));
const account: AccountOut = {
  id: "account-1",
  name: "Example partner",
  status: "active",
  tier: "bronze",
  allowed_promotion_kinds: ["product"],
  effective_promotion_kind: "product",
  promotion_kind_source: "tier",
  benefits: [],
  organic_enabled: true,
  auto_include_products: true,
  promotion_surfaces: ["article", "feed"],
  advanced_targeting: false,
  allowed_ad_formats: ["feed"],
};
const paidAccount: AccountOut = {
  ...account,
  tier: "gold",
  allowed_promotion_kinds: ["product", "ad"],
  effective_promotion_kind: "ad",
  allowed_ad_formats: ["feed", "homepage_sponsorship", "sponsored_content", "engagement"],
};
const product = {
  id: "product-1",
  name: "Kubernetes helper",
  technologies: ["Kubernetes"],
  status: "approved",
  excluded: false,
  globally_excluded: false,
  ad_image_url: null,
};
const adImageDefaults = {
  ad_image_url: null,
  image_url: null,
  default_image_url: null,
  ad_image_status: "default" as const,
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
const destinationProduct = {
  id: product.id,
  name: product.name,
  product_url: "https://tool.example/console?plan=pro&utm_source=devfeed#setup",
  listing_url:
    "https://partner.example/products/kubernetes-helper?ref=partner&utm_source=devfeed#details",
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.mocked(api.adminPartnerCampaignsList).mockResolvedValue({ items: [campaign], total: 1 });
  vi.mocked(api.adminPartnerCampaignGet).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerCampaignCreate).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerCampaignUpdate).mockResolvedValue(campaign);
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({ items: [product], total: 1 });
  vi.mocked(api.adminPartnerProductPromotionUpdate).mockResolvedValue({
    ...adImageDefaults,
    account_id: account.id,
    product_id: product.id,
    excluded: true,
  });
  vi.mocked(api.adminPartnerDestinationPreview).mockResolvedValue({ product: null });
  vi.mocked(api.adminPartnerAudienceTopics).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.adminPartnerAudienceTopicsCatalog).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.adminPartnerCreativePreview).mockResolvedValue({ product: null });
  vi.mocked(api.adminPartnerSponsorArticles).mockResolvedValue({ items: [], total: 0 });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function fillDates() {
  fireEvent.change(screen.getByLabelText(/^Starts/), { target: { value: "2026-10-01T10:00" } });
  fireEvent.change(screen.getByLabelText(/^Ends/), { target: { value: "2026-11-01T10:00" } });
}

const seniorAudience = {
  experience_levels: ["senior" as const],
  language_topic_ids: [],
  tool_topic_ids: [],
};
const advancedAccount: AccountOut = {
  ...paidAccount,
  tier: "platinum",
  advanced_targeting: true,
  audience: seniorAudience,
};
const advancedTiers = [
  {
    tier: "platinum",
    advanced_targeting: true,
    benefits: ["Advanced audience targeting"],
    allowed_promotion_kinds: ["product", "ad"],
    default_promotion_kind: "ad",
  },
  {
    tier: "gold",
    advanced_targeting: false,
    benefits: [],
    allowed_promotion_kinds: ["product", "ad"],
    default_promotion_kind: "ad",
  },
];

it("saves advanced account defaults and preserves restricted audiences after a tier downgrade", async () => {
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT" ? Response.json(advancedAccount) : Response.json(advancedTiers),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={advancedAccount} />);
  await screen.findByRole("checkbox", { name: "Senior" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Entry level" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Partnership tier" }));
  fireEvent.click(screen.getByRole("option", { name: "Gold" }));
  expect(screen.queryByRole("checkbox", { name: "Senior" })).toBeNull();
  expect(screen.getByText(/Saved restrictions are retained/)).toBeTruthy();
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    tier: "gold",
    audience: { ...seniorAudience, experience_levels: ["senior", "entry_level"] },
  });
});

it("allows an administrator to explicitly clear saved restrictions on a lower tier", async () => {
  const current = { ...account, audience: seniorAudience };
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT" ? Response.json(current) : Response.json(advancedTiers),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={current} />);
  fireEvent.click(screen.getByRole("button", { name: "Clear saved audience restrictions" }));
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body)).audience).toEqual({
    experience_levels: [],
    language_topic_ids: [],
    tool_topic_ids: [],
  });
});

it("uses the bounded global audience catalog before a new account has an ID", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(advancedTiers)),
  );
  renderAdmin(<PartnerAccountManagement mode="account" />);
  fireEvent.click(screen.getByRole("combobox", { name: "Partnership tier" }));
  fireEvent.click(screen.getByRole("option", { name: "Platinum" }));
  fireEvent.click(await screen.findByRole("combobox", { name: "Programming languages" }));
  await waitFor(() =>
    expect(api.adminPartnerAudienceTopicsCatalog).toHaveBeenCalledWith(
      { dimension: "languages", q: "", offset: 0, limit: 25 },
      { signal: expect.any(AbortSignal) },
    ),
  );
  expect(api.adminPartnerAudienceTopics).not.toHaveBeenCalled();
});

it.each(["inherit", "custom"])(
  "saves an explicit %s campaign audience without changing product targeting",
  async (mode) => {
    renderAdmin(<PartnerCampaignPage account={advancedAccount} />);
    fireEvent.change(screen.getByLabelText(/Campaign name/), {
      target: { value: "Audience campaign" },
    });
    fillDates();
    if (mode === "custom") {
      fireEvent.click(screen.getByRole("combobox", { name: "Campaign audience" }));
      fireEvent.click(screen.getByRole("option", { name: "Customize audience" }));
      expect((screen.getByRole("checkbox", { name: "Senior" }) as HTMLInputElement).checked).toBe(
        true,
      );
      fireEvent.click(screen.getByRole("checkbox", { name: "Senior" }));
    } else {
      expect(screen.queryByRole("checkbox", { name: "Senior" })).toBeNull();
      expect(screen.getByText("Senior")).toBeTruthy();
    }
    fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
    await waitFor(() => expect(api.adminPartnerCampaignCreate).toHaveBeenCalled());
    expect(vi.mocked(api.adminPartnerCampaignCreate).mock.calls[0][1]).toMatchObject({
      targeting: "all",
      audience:
        mode === "inherit"
          ? null
          : { experience_levels: [], language_topic_ids: [], tool_topic_ids: [] },
    });
  },
);

it("keeps a lower-tier campaign's custom audience readonly and unchanged on unrelated edits", async () => {
  vi.mocked(api.adminPartnerCampaignGet).mockResolvedValue({
    ...campaign,
    audience: seniorAudience,
  });
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId={campaign.id} />);
  await screen.findByText("Custom campaign audience.");
  expect(screen.queryByRole("combobox", { name: "Campaign audience" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Senior" })).toBeNull();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() => expect(api.adminPartnerCampaignUpdate).toHaveBeenCalled());
  expect(vi.mocked(api.adminPartnerCampaignUpdate).mock.calls[0][2].audience).toEqual(
    seniorAudience,
  );
});

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
  expect(screen.queryByLabelText(/Product exposure limit/)).toBeNull();
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1"));
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    organic_enabled: false,
    auto_include_products: false,
    promotion_surfaces: ["article"],
  });
  expect(JSON.parse(String(write?.[1]?.body))).not.toHaveProperty("frequency_cap");
  expect(screen.queryByRole("combobox", { name: "Type" })).toBeNull();
});

const structuredTiers = [
  {
    tier: "bronze",
    benefits: ["Contextual recommendations"],
    allowed_promotion_kinds: ["product"],
    default_promotion_kind: "product",
  },
  {
    tier: "gold",
    benefits: ["Sponsored catalog"],
    allowed_promotion_kinds: ["product", "ad"],
    default_promotion_kind: "ad",
  },
];

it.each([
  { tier: "bronze", inherited: "Product recommendation", kinds: ["Product recommendation"] },
  { tier: "gold", inherited: "Sponsored", kinds: ["Product recommendation", "Sponsored"] },
])(
  "offers only valid account defaults and preserves tier inheritance for $tier",
  async (policy) => {
    const current = { ...account, tier: policy.tier } as AccountOut;
    const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
      init?.method === "PUT" ? Response.json(current) : Response.json(structuredTiers),
    );
    vi.stubGlobal("fetch", fetcher);
    renderAdmin(<PartnerAccountManagement mode="account" account={current} />);
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Default promotion" }).hasAttribute("disabled"),
      ).toBe(false),
    );
    const defaults = [
      { label: "Partnership tier", choices: ["Bronze", "Silver", "Gold", "Platinum", "Diamond"] },
      { label: "Status", choices: ["Active", "Paused"] },
      {
        label: "Default promotion",
        choices: [`Inherit tier (${policy.inherited})`, ...policy.kinds],
      },
      {
        label: "Imported product acceptance",
        choices: ["Automatic capability checks", "Trust connected partner imports"],
      },
      { label: "Product click destination", choices: ["Product website", "Partner listing"] },
    ];
    for (const setting of defaults) {
      const trigger = screen.getByRole("combobox", { name: setting.label });
      expect(trigger.getAttribute("aria-required")).toBe("true");
      fireEvent.click(trigger);
      expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(
        setting.choices,
      );
      expect(screen.queryByRole("option", { name: "None" })).toBeNull();
      fireEvent.click(
        screen.getByRole("option", { name: trigger.textContent ?? setting.choices[0] }),
      );
    }
    fireEvent.click(screen.getByRole("combobox", { name: "Default promotion" }));
    fireEvent.click(screen.getByRole("option", { name: "Product recommendation" }));
    expect(screen.getByText(/Effective: Product recommendation · account override/)).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox", { name: "Default promotion" }));
    fireEvent.click(screen.getByRole("option", { name: `Inherit tier (${policy.inherited})` }));
    expect(
      screen.getByText(new RegExp(`Effective: ${policy.inherited} · inherited from tier`)),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox", { name: "Imported product acceptance" }));
    fireEvent.click(screen.getByRole("option", { name: "Trust connected partner imports" }));
    fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
    await waitFor(() => expect(push).toHaveBeenCalled());
    const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
    expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
      tier: policy.tier,
      promotion_kind: null,
      approval_mode: "trusted",
    });
  },
);

it("offers only valid campaign status and targeting choices", () => {
  renderAdmin(<PartnerCampaignPage account={paidAccount} />);
  for (const setting of [
    { label: "Campaign status", choices: ["Draft", "Active", "Paused", "Ended"] },
    {
      label: "Product targeting",
      choices: [
        "All eligible account products",
        "Selected account products",
        "Products matching topics or technologies",
      ],
    },
  ]) {
    const trigger = screen.getByRole("combobox", { name: setting.label });
    expect(trigger.getAttribute("aria-required")).toBe("true");
    fireEvent.click(trigger);
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(
      setting.choices,
    );
    fireEvent.click(
      screen.getByRole("option", { name: trigger.textContent ?? setting.choices[0] }),
    );
  }
});

it.each([3, 20])(
  "ignores a legacy exposure limit of %s when saving other account settings",
  async (limit) => {
    const legacyAccount = { ...account, frequency_cap: limit };
    const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
      init?.method === "PUT" ? Response.json(account) : Response.json(structuredTiers),
    );
    vi.stubGlobal("fetch", fetcher);
    renderAdmin(<PartnerAccountManagement mode="account" account={legacyAccount} />);
    expect(screen.queryByLabelText(/Product exposure limit|Frequency cap/i)).toBeNull();
    expect(document.querySelector('[name="frequency_cap"]')).toBeNull();
    fireEvent.click(screen.getByLabelText(/Allow ordinary recommendations/));
    fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/partnerships/accounts/account-1"));
    const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
    expect(write?.[1]?.headers).toEqual({
      "content-type": "application/json",
      "x-csrf-token": "test-csrf",
    });
    expect(JSON.parse(String(write?.[1]?.body))).toEqual({
      name: account.name,
      tier: "bronze",
      status: "active",
      organic_enabled: false,
      auto_include_products: true,
      promotion_surfaces: ["article", "feed"],
      promotion_kind: null,
      approval_mode: "qualified",
      outbound_destination: "product_url",
      audience: { experience_levels: [], language_topic_ids: [], tool_topic_ids: [] },
      ad_formats: ["feed"],
    });
  },
);

it("shows only the accepted account product's final URL and switches destinations before saving", async () => {
  vi.mocked(api.adminPartnerDestinationPreview).mockResolvedValue({ product: destinationProduct });
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(structuredTiers));
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={account} />);
  const preview = await screen.findByRole("region", { name: "Click destination preview" });
  const link = await within(preview).findByRole("link", { name: destinationProduct.product_url });
  expect(link.getAttribute("href")).toBe(destinationProduct.product_url);
  expect(within(preview).getByText(destinationProduct.product_url)).toBeTruthy();
  expect(link.textContent).toBe(destinationProduct.product_url);
  expect(within(preview).queryByText(destinationProduct.name)).toBeNull();
  expect(preview.querySelector("img")).toBeNull();
  fireEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "Product website",
    "Partner listing",
  ]);
  expect(screen.queryByRole("option", { name: "None" })).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  expect(link.getAttribute("href")).toBe(destinationProduct.listing_url);
  expect(link.textContent).toBe(destinationProduct.listing_url);
  expect(within(preview).getByText(destinationProduct.listing_url)).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  fireEvent.click(screen.getByRole("option", { name: "Product website" }));
  expect(link.getAttribute("href")).toBe(destinationProduct.product_url);
  expect(api.adminPartnerDestinationPreview).toHaveBeenCalledExactlyOnceWith(
    account.id,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  expect(push).not.toHaveBeenCalled();
});

it.each(["empty", "new"])("shows an honest %s-account destination preview", async (kind) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(structuredTiers)),
  );
  renderAdmin(
    <PartnerAccountManagement mode="account" account={kind === "new" ? undefined : account} />,
  );
  const preview = screen.getByRole("region", { name: "Click destination preview" });
  await within(preview).findByText(
    "A preview will appear when this account has an accepted product.",
  );
  expect(within(preview).queryByRole("link")).toBeNull();
  if (kind === "new") expect(api.adminPartnerDestinationPreview).not.toHaveBeenCalled();
  else
    expect(api.adminPartnerDestinationPreview).toHaveBeenCalledWith(account.id, expect.anything());
});

it("explains a missing partner listing without retaining the product-website link", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(structuredTiers)),
  );
  vi.mocked(api.adminPartnerDestinationPreview).mockResolvedValue({
    product: { ...destinationProduct, listing_url: null },
  });
  renderAdmin(<PartnerAccountManagement mode="account" account={account} />);
  const preview = screen.getByRole("region", { name: "Click destination preview" });
  await within(preview).findByRole("link", { name: destinationProduct.product_url });
  fireEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  fireEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  expect(within(preview).getByText("This product has no available partner listing.")).toBeTruthy();
  expect(within(preview).queryByRole("link")).toBeNull();
  expect(within(preview).queryByText(destinationProduct.name)).toBeNull();
  fireEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  fireEvent.click(screen.getByRole("option", { name: "Product website" }));
  expect(
    within(preview)
      .getByRole("link", { name: destinationProduct.product_url })
      .getAttribute("href"),
  ).toBe(destinationProduct.product_url);
});

it("shows loading and a retryable preview failure without replacing the account form", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(structuredTiers)),
  );
  let rejectPreview!: (reason: Error) => void;
  vi.mocked(api.adminPartnerDestinationPreview)
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectPreview = reject;
        }),
    )
    .mockResolvedValue({ product: destinationProduct });
  renderAdmin(<PartnerAccountManagement mode="account" account={account} />);
  const preview = screen.getByRole("region", { name: "Click destination preview" });
  expect(within(preview).getByText("Loading product preview…")).toBeTruthy();
  await act(async () => {
    rejectPreview(new Error("Temporary service failure"));
  });
  await within(preview).findByText("Could not load the product preview.");
  expect(screen.getByRole("form", { name: "Partnership settings" })).toBeTruthy();
  fireEvent.click(within(preview).getByRole("button", { name: "Retry preview" }));
  await within(preview).findByRole("link", { name: destinationProduct.product_url });
  expect(api.adminPartnerDestinationPreview).toHaveBeenCalledTimes(2);
});

it("aborts old-account preview requests and ignores their late responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(structuredTiers)),
  );
  let resolveFirst!: (value: { product: typeof destinationProduct }) => void;
  vi.mocked(api.adminPartnerDestinationPreview)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockResolvedValue({
      product: {
        ...destinationProduct,
        id: "second-product",
        name: "Second partner tool",
        product_url: "https://second.example/?utm_source=devfeed#docs",
      },
    });
  function Accounts() {
    const [selected, setSelected] = useState(account);
    return (
      <>
        <button onClick={() => setSelected({ ...account, id: "account-2" })}>Switch account</button>
        <PartnerAccountManagement mode="account" account={selected} />
      </>
    );
  }
  const view = renderAdmin(<Accounts />);
  await waitFor(() => expect(api.adminPartnerDestinationPreview).toHaveBeenCalledTimes(1));
  const firstSignal = vi.mocked(api.adminPartnerDestinationPreview).mock.calls[0][1]?.signal;
  fireEvent.click(screen.getByRole("button", { name: "Switch account" }));
  const preview = screen.getByRole("region", { name: "Click destination preview" });
  await within(preview).findByRole("link", {
    name: "https://second.example/?utm_source=devfeed#docs",
  });
  expect(firstSignal?.aborted).toBe(true);
  await act(async () => {
    resolveFirst({ product: destinationProduct });
  });
  expect(within(preview).queryByRole("link", { name: destinationProduct.product_url })).toBeNull();
  expect(
    within(preview).getByRole("link", { name: "https://second.example/?utm_source=devfeed#docs" }),
  ).toBeTruthy();
  expect(api.adminPartnerDestinationPreview).toHaveBeenLastCalledWith(
    "account-2",
    expect.anything(),
  );
  const latestSignal = vi.mocked(api.adminPartnerDestinationPreview).mock.calls.at(-1)?.[1]?.signal;
  view.unmount();
  expect(latestSignal?.aborted).toBe(true);
});

it("inherits Sponsored from the structured tier and saves account-wide trust and click destination", async () => {
  const gold = {
    ...account,
    tier: "gold",
    promotion_kind: null,
    approval_mode: "qualified",
    outbound_destination: "product_url",
  } as AccountOut;
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT" ? Response.json(gold) : Response.json(structuredTiers),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={gold} />);
  await screen.findByText("Sponsored catalog");
  expect(screen.getByText(/Effective: Sponsored · inherited from tier/)).toBeTruthy();
  expect(
    (screen.getByLabelText(/Allow ordinary recommendations/) as HTMLInputElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("combobox", { name: "Imported product acceptance" }));
  fireEvent.click(screen.getByRole("option", { name: "Trust connected partner imports" }));
  fireEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  fireEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    promotion_kind: null,
    approval_mode: "trusted",
    outbound_destination: "listing_url",
    organic_enabled: true,
  });
  expect(write?.[1]?.headers).toEqual({
    "content-type": "application/json",
    "x-csrf-token": "test-csrf",
  });
});

it("limits promotion overrides to tier permissions and returns to inheritance on downgrade", async () => {
  const gold = { ...account, tier: "gold", promotion_kind: "ad" } as AccountOut;
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT" ? Response.json(account) : Response.json(structuredTiers),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={gold} />);
  await screen.findByText("Sponsored catalog");
  fireEvent.click(screen.getByRole("combobox", { name: "Partnership tier" }));
  fireEvent.click(screen.getByRole("option", { name: "Bronze" }));
  expect(screen.getByText(/Effective: Product recommendation · inherited from tier/)).toBeTruthy();
  fireEvent.click(screen.getByRole("combobox", { name: "Default promotion" }));
  expect(screen.queryByRole("option", { name: "Sponsored" })).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "Inherit tier (Product recommendation)" }));
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    tier: "bronze",
    promotion_kind: null,
  });
});

it("creates a finite paid catalog campaign inheriting the account's allowed placements", async () => {
  renderAdmin(<PartnerCampaignPage account={paidAccount} />);
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

it("saves Silver automatic format defaults without fetching creatives eagerly", async () => {
  const current: AccountOut = { ...paidAccount, tier: "silver", ad_formats: ["feed"] };
  const fetcher = vi.fn(async (_input: unknown, init?: RequestInit) =>
    init?.method === "PUT" ? Response.json(current) : Response.json([]),
  );
  vi.stubGlobal("fetch", fetcher);
  renderAdmin(<PartnerAccountManagement mode="account" account={current} />);
  expect(api.adminPartnerCreativePreview).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Homepage sponsorship/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /^Engagement/ }));
  expect(screen.queryByRole("checkbox", { name: /^Sponsored content/ })).toBeNull();
  fireEvent.submit(screen.getByRole("form", { name: "Partnership settings" }));
  await waitFor(() => expect(push).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(write?.[1]?.body)).ad_formats).toEqual([
    "feed",
    "homepage_sponsorship",
    "engagement",
  ]);
});

it("selects a real approved article for Silver sponsored content while retaining product targeting", async () => {
  const article: ArticleOut = {
    id: "article-1",
    title: "Reliable Kubernetes jobs",
    slug: "reliable-jobs",
    summary: "Scheduled jobs that work.",
    canonical_url: "https://publisher.example/jobs",
    image_url: null,
    author: null,
    content_type: "article",
    discovered_at: "2026-10-01T00:00:00Z",
    feed_at: "2026-10-01T00:00:00Z",
    language: "en",
    origins: [],
    published_at: "2026-10-01T00:00:00Z",
    sources: [],
    tags: [],
  };
  vi.mocked(api.adminPartnerSponsorArticles).mockResolvedValue({ items: [article], total: 1 });
  renderAdmin(
    <PartnerCampaignPage
      account={{ ...paidAccount, tier: "silver", promotion_surfaces: ["article"] }}
    />,
  );
  fireEvent.change(screen.getByLabelText(/Campaign name/), {
    target: { value: "Sponsored tutorial" },
  });
  fillDates();
  fireEvent.click(screen.getByRole("combobox", { name: "Ad format" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "Feed",
    "Homepage sponsorship",
    "Sponsored content",
    "Engagement",
  ]);
  fireEvent.click(screen.getByRole("option", { name: "Sponsored content" }));
  expect(
    screen.getByText(/Delivery also requires the article topics to match the current feed/),
  ).toBeTruthy();
  expect(screen.getByText(/Reader feeds are disabled/)).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Product targeting" })).toBeTruthy();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("Choose an approved article for sponsored content.");
  expect(api.adminPartnerCampaignCreate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Sponsored article" }));
  fireEvent.click(await screen.findByRole("option", { name: /Reliable Kubernetes jobs/ }));
  expect(api.adminPartnerSponsorArticles).toHaveBeenCalledWith(
    account.id,
    expect.objectContaining({ offset: 0, limit: 25 }),
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() => expect(api.adminPartnerCampaignCreate).toHaveBeenCalled());
  expect(api.adminPartnerCampaignCreate).toHaveBeenCalledWith(
    account.id,
    expect.objectContaining({
      ad_format: "sponsored_content",
      article_id: article.id,
      surfaces: ["feed"],
      targeting: "all",
    }),
    expect.anything(),
  );
});

it("saves homepage sponsorship with feed-only surfaces and clears obsolete article selection", async () => {
  vi.mocked(api.adminPartnerCampaignGet).mockResolvedValue({
    ...campaign,
    ad_format: "sponsored_content",
    article_id: "article-1",
  });
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId={campaign.id} />);
  await screen.findByDisplayValue(campaign.name);
  fireEvent.click(screen.getByRole("combobox", { name: "Ad format" }));
  fireEvent.click(screen.getByRole("option", { name: "Homepage sponsorship" }));
  expect(screen.queryByRole("combobox", { name: "Sponsored article" })).toBeNull();
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await waitFor(() => expect(api.adminPartnerCampaignUpdate).toHaveBeenCalled());
  expect(api.adminPartnerCampaignUpdate).toHaveBeenCalledWith(
    account.id,
    campaign.id,
    expect.objectContaining({
      ad_format: "homepage_sponsorship",
      article_id: null,
      surfaces: ["feed"],
    }),
    expect.anything(),
  );
});

it("updates product restrictions with admin CSRF without toggling its exclusion", async () => {
  const policy = {
    ...adImageDefaults,
    account_id: account.id,
    product_id: product.id,
    excluded: true,
    ad_formats: null,
    promotion_surfaces: null,
  };
  vi.mocked(api.adminPartnerProductPromotionGet).mockResolvedValue(policy);
  vi.mocked(api.adminPartnerProductPromotionUpdate).mockResolvedValue(policy);
  renderAdmin(
    <PartnerProductPlacements
      accountId={account.id}
      product={product}
      allowedFormats={paidAccount.allowed_ad_formats}
      onSaved={push}
    />,
  );
  expect(api.adminPartnerProductPromotionGet).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: `Edit placements for ${product.name}` }));
  await screen.findByRole("combobox", { name: "Product formats" });
  fireEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(api.adminPartnerProductPromotionUpdate).toHaveBeenCalled());
  expect(api.adminPartnerProductPromotionUpdate).toHaveBeenCalledWith(
    account.id,
    product.id,
    { excluded: true, ad_formats: null, promotion_surfaces: null, ad_image_url: null },
    expect.objectContaining({
      headers: { "X-CSRF-Token": "test-csrf" },
      signal: expect.any(AbortSignal),
    }),
  );
});

it("uploads ad images through the admin gateway with CSRF without closing placements", async () => {
  vi.mocked(api.adminPartnerProductPromotionGet).mockResolvedValue({
    ...adImageDefaults,
    account_id: account.id,
    product_id: product.id,
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
  });
  const uploadedUrl = "https://images.example/partner-ad-images/800.webp?v=admin-upload";
  vi.mocked(api.adminPartnerProductAdImageUpload).mockResolvedValue({
    ...adImageDefaults,
    account_id: account.id,
    product_id: product.id,
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: uploadedUrl,
    image_url: uploadedUrl,
    ad_image_status: "ready",
  });
  renderAdmin(
    <PartnerProductPlacements
      accountId={account.id}
      product={product}
      allowedFormats={paidAccount.allowed_ad_formats}
      onSaved={push}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: `Edit placements for ${product.name}` }));
  await screen.findByRole("combobox", { name: "Ad image" });
  expect(screen.queryByRole("button", { name: "Upload image" })).toBeNull();
  fireEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  fireEvent.click(screen.getByRole("option", { name: "Custom image" }));
  const file = new File(["png"], "ad.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Upload ad image file"), { target: { files: [file] } });
  await screen.findByText(/Image uploaded and applied/);
  expect(api.adminPartnerProductAdImageUpload).toHaveBeenCalledWith(
    account.id,
    product.id,
    { file },
    expect.objectContaining({
      headers: { "X-CSRF-Token": "test-csrf" },
      signal: expect.any(AbortSignal),
    }),
  );
  expect(push).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it.each([false, true])(
  "requests an exact admin product preview and rejects unrelated creative (%s)",
  async (wrongProduct) => {
    vi.mocked(api.adminPartnerProductPromotionGet).mockResolvedValue({
      ...adImageDefaults,
      account_id: account.id,
      product_id: product.id,
      excluded: false,
      ad_formats: null,
      promotion_surfaces: null,
    });
    vi.mocked(api.adminPartnerCreativePreview).mockResolvedValue({
      product: {
        ...destinationProduct,
        id: wrongProduct ? "other-product" : product.id,
        name: wrongProduct ? "Other product" : product.name,
        description: "Manage Kubernetes workloads.",
        logo_url: null,
        image_url: null,
        ad_image_status: "default",
      },
    });
    renderAdmin(
      <PartnerProductPlacements
        accountId={account.id}
        product={product}
        allowedFormats={paidAccount.allowed_ad_formats}
        onSaved={push}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: `Edit placements for ${product.name}` }));
    await screen.findByRole("combobox", { name: "Product availability" });
    expect(api.adminPartnerCreativePreview).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Preview this product" }));
    const preview = await screen.findByRole("region", { name: "Creative preview" });
    if (wrongProduct) {
      await within(preview).findByText("Could not load the creative preview.");
      expect(screen.queryByText("Other product")).toBeNull();
    } else await within(preview).findByText(product.name);
    expect(api.adminPartnerCreativePreview).toHaveBeenCalledWith(
      account.id,
      { product_id: product.id },
      { signal: expect.any(AbortSignal) },
    );
  },
);

it("rejects an admin product policy for another product", async () => {
  vi.mocked(api.adminPartnerProductPromotionGet).mockResolvedValue({
    ...adImageDefaults,
    account_id: account.id,
    product_id: "other-product",
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
  });
  renderAdmin(
    <PartnerProductPlacements
      accountId={account.id}
      product={product}
      allowedFormats={paidAccount.allowed_ad_formats}
      onSaved={push}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: `Edit placements for ${product.name}` }));
  await screen.findByText("Could not confirm product placement settings.");
  expect(screen.queryByRole("button", { name: "Save product placements" })).toBeNull();
});

it("aborts a closed admin preview and ignores its late unauthorized failure", async () => {
  const login = vi.spyOn(client, "returnToLogin").mockImplementation(() => {});
  let rejectPreview!: (error: Error) => void;
  vi.mocked(api.adminPartnerCreativePreview).mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectPreview = reject;
      }),
  );
  vi.mocked(api.adminPartnerProductPromotionGet).mockResolvedValue({
    ...adImageDefaults,
    account_id: account.id,
    product_id: product.id,
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
  });
  renderAdmin(
    <PartnerProductPlacements
      accountId={account.id}
      product={product}
      allowedFormats={paidAccount.allowed_ad_formats}
      onSaved={push}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: `Edit placements for ${product.name}` }));
  await screen.findByRole("combobox", { name: "Product availability" });
  fireEvent.click(screen.getByRole("button", { name: "Preview this product" }));
  await waitFor(() => expect(api.adminPartnerCreativePreview).toHaveBeenCalledOnce());
  const signal = vi.mocked(api.adminPartnerCreativePreview).mock.calls[0][2]?.signal;
  fireEvent.click(screen.getByRole("button", { name: "Close placements" }));
  expect(signal?.aborted).toBe(true);
  await act(async () => rejectPreview(new ApiError(401)));
  expect(login).not.toHaveBeenCalled();
  expect(push).not.toHaveBeenCalled();
});

it("selects products only from this account's inventory and rejects an empty selection", async () => {
  renderAdmin(<PartnerCampaignPage account={paidAccount} />);
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
  renderAdmin(<PartnerCampaignPage account={paidAccount} />);
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
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId={campaign.id} />);
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
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId="foreign-campaign" />);
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
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId={campaign.id} />);
  await screen.findByDisplayValue(campaign.name);
  fireEvent.submit(screen.getByRole("form", { name: "Paid campaign settings" }));
  await screen.findByText("daily_impression_limit: Use a positive impression limit.");
  expect(screen.getByDisplayValue(campaign.name)).toBeTruthy();
  expect(push).not.toHaveBeenCalled();
});

it("shows campaign attribution totals and scoped edit links", async () => {
  renderAdmin(<PartnerCampaigns account={paidAccount} />);
  expect((await screen.findByRole("link", { name: campaign.name })).getAttribute("href")).toBe(
    "/partnerships/accounts/account-1/campaigns/campaign-1/edit",
  );
  expect(screen.getByText("5.00%")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Create paid campaign" }).getAttribute("href")).toContain(
    "campaigns/new",
  );
});

it("routes the standing sponsored campaign to account defaults and keeps its form read-only", async () => {
  const managed = { ...campaign, managed: true };
  vi.mocked(api.adminPartnerCampaignsList).mockResolvedValue({ items: [managed], total: 1 });
  const view = renderAdmin(<PartnerCampaigns account={paidAccount} />);
  expect(
    (await screen.findByRole("link", { name: /October promotion.*Account default/ })).getAttribute(
      "href",
    ),
  ).toBe("/partnerships/accounts/account-1/edit");
  expect(screen.getByText("Follows account defaults")).toBeTruthy();
  view.unmount();
  vi.mocked(api.adminPartnerCampaignGet).mockResolvedValue(managed);
  renderAdmin(<PartnerCampaignPage account={paidAccount} campaignId={managed.id} />);
  await screen.findByRole("region", { name: "Inherited sponsored placement" });
  expect(screen.queryByRole("form", { name: "Paid campaign settings" })).toBeNull();
  expect(screen.getByRole("link", { name: "Edit account defaults" }).getAttribute("href")).toBe(
    "/partnerships/accounts/account-1/edit",
  );
  expect(api.adminPartnerCampaignUpdate).not.toHaveBeenCalled();
});

it("retains lower-tier dated campaign reporting while blocking new campaigns and edits", async () => {
  const list = renderAdmin(<PartnerCampaigns account={account} />);
  await screen.findByRole("link", { name: campaign.name });
  expect(screen.queryByRole("link", { name: "Create paid campaign" })).toBeNull();
  expect(
    screen.getByText(/sponsored delivery and campaign changes require Silver or higher/),
  ).toBeTruthy();
  list.unmount();
  const form = renderAdmin(<PartnerCampaignPage account={account} campaignId={campaign.id} />);
  await screen.findByRole("region", { name: "Sponsored campaign availability" });
  expect(screen.queryByRole("form", { name: "Paid campaign settings" })).toBeNull();
  expect(screen.getByText(/retained for reporting/)).toBeTruthy();
  expect(
    screen.getByText(/Sponsored delivery and campaign changes require Silver or higher/),
  ).toBeTruthy();
  form.unmount();
  renderAdmin(<PartnerCampaignPage account={account} />);
  await screen.findByRole("region", { name: "Sponsored campaign availability" });
  expect(screen.queryByRole("form", { name: "Paid campaign settings" })).toBeNull();
  expect(api.adminPartnerCampaignCreate).not.toHaveBeenCalled();
  expect(api.adminPartnerCampaignUpdate).not.toHaveBeenCalled();
});

it("searches account products and excludes them with CSRF while preserving inherited defaults", async () => {
  renderAdmin(<PartnerInventory account={account} />);
  await screen.findByRole("link", { name: product.name });
  expect(screen.getByText("Inherits account defaults")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Product name" }), {
    target: { value: "Kubernetes" },
  });
  fireEvent.submit(screen.getByRole("search", { name: "Product name" }));
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

it("automatically searches only the latest text after typing pauses and resets pagination", async () => {
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({ items: [product], total: 60 });
  renderAdmin(<PartnerInventory account={account} />);
  await screen.findByRole("link", { name: product.name });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(api.adminPartnerInventoryList).toHaveBeenLastCalledWith(
      account.id,
      { offset: 25, limit: 25, q: undefined },
      expect.anything(),
    ),
  );
  vi.useFakeTimers();
  vi.mocked(api.adminPartnerInventoryList).mockClear();
  const input = screen.getByRole("textbox", { name: "Product name" });
  input.focus();
  fireEvent.change(input, { target: { value: "Ku" } });
  await act(() => vi.advanceTimersByTimeAsync(150));
  fireEvent.change(input, { target: { value: " Kubernetes " } });
  await act(() => vi.advanceTimersByTimeAsync(249));
  expect(api.adminPartnerInventoryList).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(api.adminPartnerInventoryList).toHaveBeenCalledExactlyOnceWith(
    account.id,
    { offset: 0, limit: 25, q: "Kubernetes" },
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(document.activeElement).toBe(input);
  expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
});

it("searches immediately on Enter without a duplicate request and restores all products when cleared", async () => {
  renderAdmin(<PartnerInventory account={account} />);
  await screen.findByRole("link", { name: product.name });
  vi.useFakeTimers();
  vi.mocked(api.adminPartnerInventoryList).mockClear();
  const input = screen.getByRole("textbox", { name: "Product name" });
  fireEvent.change(input, { target: { value: " PostgreSQL " } });
  fireEvent.submit(screen.getByRole("search", { name: "Product name" }));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(api.adminPartnerInventoryList).toHaveBeenCalledExactlyOnceWith(
    account.id,
    { offset: 0, limit: 25, q: "PostgreSQL" },
    expect.anything(),
  );
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(api.adminPartnerInventoryList).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Clear product name" }));
  await act(() => vi.advanceTimersByTimeAsync(0));
  expect(api.adminPartnerInventoryList).toHaveBeenLastCalledWith(
    account.id,
    { offset: 0, limit: 25, q: undefined },
    expect.anything(),
  );
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(api.adminPartnerInventoryList).toHaveBeenCalledTimes(2);
  expect(document.activeElement).toBe(input);
});

it("cancels an unsent inventory search when leaving the Products tab", async () => {
  const view = renderAdmin(<PartnerInventory account={account} />);
  await screen.findByRole("link", { name: product.name });
  vi.useFakeTimers();
  vi.mocked(api.adminPartnerInventoryList).mockClear();
  fireEvent.change(screen.getByRole("textbox", { name: "Product name" }), {
    target: { value: "Kubernetes" },
  });
  view.unmount();
  await act(() => vi.advanceTimersByTimeAsync(500));
  expect(api.adminPartnerInventoryList).not.toHaveBeenCalled();
});

it("makes trusted account acceptance primary while keeping the pending developer check separate", async () => {
  vi.mocked(api.adminPartnerInventoryList).mockResolvedValue({
    items: [
      { ...product, status: "pending", commercial_approved: true, approval_source: "trusted" },
    ],
    total: 1,
  });
  renderAdmin(<PartnerInventory account={account} />);
  const link = await screen.findByRole("link", { name: product.name });
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "Product",
    "Acceptance",
    "Technologies",
    "Promotion",
    "Actions",
  ]);
  const cells = within(link.closest("tr")!).getAllByRole("cell");
  expect(within(cells[0]).getByText(/pending/i)).toBeTruthy();
  expect(within(cells[1]).getByText("Accepted", { exact: true })).toBeTruthy();
  expect(within(cells[1]).getByText("Trusted partner", { exact: true })).toBeTruthy();
  expect(screen.queryByText("Awaiting acceptance", { exact: true })).toBeNull();
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

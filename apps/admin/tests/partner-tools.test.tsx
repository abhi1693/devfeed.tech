// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderAdmin } from "./render-admin";
import { PartnerConnectionForm } from "@/components/organisms/partner-connection-form";
import { PartnerConnections } from "@/components/organisms/partner-connections";
import { PartnerProductPage } from "@/components/organisms/partner-product-page";
import { PartnerTools } from "@/components/organisms/partner-tools";
import { PartnerRuns } from "@/components/organisms/partner-runs";
import * as api from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { connectorDefaults } from "@/lib/partner-connectors";
import type { AccountOut, ConnectionOut, ProductOut } from "@/lib/api/generated/models";

const replace = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/api/generated/admin", () => ({
  accountsV1AdminPartnerAccountsGet: vi.fn(),
  dashboardV1AdminPartnerAccountsAccountIdDashboardGet: vi.fn(),
  adminPartnerToolsList: vi.fn(),
  adminPartnerConnectionsList: vi.fn(),
  adminPartnerProvidersList: vi.fn(),
  adminPartnerConnectionCreate: vi.fn(),
  adminPartnerConnectorPreview: vi.fn(),
  adminPartnerConnectorResponse: vi.fn().mockResolvedValue({ data: { items: [] }, sampled: false }),
  adminPartnerConnectionUpdate: vi.fn(),
  adminPartnerConnectionLogoUpload: vi.fn(),
  adminPartnerConnectionLogoUpdate: vi.fn(),
  adminPartnerConnectionAction: vi.fn(),
  adminPartnerProductAction: vi.fn(),
  adminPartnerToolsEvaluations: vi.fn(),
  adminPartnerPipelineList: vi.fn(),
  adminPartnerEvaluationsList: vi.fn(),
}));
const account: AccountOut = {
  advanced_targeting: false,
  allowed_ad_formats: ["feed"],
  configurable_link_rel: false,
  link_rel_options: ["nofollow", "sponsored", "ugc", "external"],
  id: "55555555-5555-4555-8555-555555555555",
  name: "Example partner account",
  tier: "bronze",
  status: "active",
  benefits: [],
  allowed_promotion_kinds: ["product"],
  effective_promotion_kind: "product",
  promotion_kind_source: "tier",
};
const connection: ConnectionOut = {
  account_id: account.id,
  provider: "nick-launches",
  partnership_type: "launch_platform",
  name: "Nick Launches",
  api_url: "https://nicklaunches.com/api/v1/products/",
  revision: 1,
  sync_interval_minutes: 360,
  enabled: false,
  state: "disconnected",
  last_sync_at: null,
  next_sync_at: null,
  error: null,
  products: 0,
  qualified: 0,
  checking: 0,
  needs_attention: 0,
  excluded: 0,
  ai_enabled: true,
  credentials_available: true,
  credential_configured: false,
};
const product: ProductOut = {
  id: "11111111-1111-1111-1111-111111111111",
  partnership_type: "launch_platform",
  name: "API Checker",
  product_url: "https://checker.example/",
  tagline: "Detect breaking API changes before deployment.",
  technologies: [],
  evidence: [],
  pricing: "free",
  status: "pending",
  revision: 1,
  verified_at: null,
  updated_at: "2026-10-02T00:00:00Z",
  reviews: [],
  eligible: false,
  excluded: false,
  assessment: { state: "attention", reason: "Product website could not be read" },
  metadata_listing_id: "44444444-4444-4444-4444-444444444444",
  listings: [
    {
      id: "44444444-4444-4444-4444-444444444444",
      provider: "nick-launches",
      platform_name: "Nick Launches",
      external_id: "checker",
      name: "API Checker",
      product_url: "https://checker.example/",
      listing_url: "https://nicklaunches.com/products/checker/",
      tagline: "Check OpenAPI compatibility.",
      pricing: "free",
      attribution: "Via Nick Launches",
      active: true,
      connection_enabled: true,
      identity_status: "resolved",
      identity_reason: null,
      updated_at: "2026-10-02T00:00:00Z",
    },
  ],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.accountsV1AdminPartnerAccountsGet).mockImplementation(async (params) => {
    const items = account.name.toLowerCase().includes((params?.q ?? "").toLowerCase())
      ? [account]
      : [];
    return { items, total: items.length };
  });
  vi.mocked(api.dashboardV1AdminPartnerAccountsAccountIdDashboardGet).mockResolvedValue({
    account,
    assets: [],
    asset_total: 0,
    start: "2026-10-01",
    end: "2026-10-11",
    last_updated_at: null,
    totals: { clicks: 0, impressions: 0, ctr: null, measured_days: 0 },
    trend: [],
  });
  vi.mocked(api.adminPartnerProvidersList).mockResolvedValue([provider]);
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue({
    items: [connection],
    total: 1,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerToolsEvaluations).mockResolvedValue([]);
  vi.mocked(api.adminPartnerPipelineList).mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerEvaluationsList).mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
});
afterEach(cleanup);

const provider = {
  provider: "nick-launches",
  name: "Nick Launches",
  partnership_type: "launch_platform" as const,
  api_url: connection.api_url,
  description: "Sync developer products from Nick Launches.",
  credentials_available: true,
};
const shipyardProvider = {
  provider: "shipyardhq",
  name: "ShipyardHQ",
  partnership_type: "launch_platform" as const,
  api_url: "https://shipyardhq.dev/api/products",
  description: "Sync developer products from ShipyardHQ.",
  credentials_available: true,
};

async function selectAccount() {
  const user = userEvent.setup();
  const trigger = screen.getByRole("combobox", { name: "Partner account" });
  await user.click(trigger);
  await user.click(await screen.findByRole("option", { name: new RegExp(account.name) }));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(trigger.textContent).toContain(account.name);
  expect((document.querySelector('select[name="account_id"]') as HTMLSelectElement).value).toBe(
    account.id,
  );
}

const customMappingPaths = {
  "Product ID paths": "id",
  "Product name paths": "name",
  "Website URL paths": "url",
  "Listing URL paths": "listing_url",
  "Tagline paths": "description",
  "Pricing paths": "pricing",
};

function configureCustomMapping() {
  fireEvent.change(screen.getByRole("textbox", { name: "Products endpoint" }), {
    target: { value: "/v4/products" },
  });
  for (const [label, value] of Object.entries(customMappingPaths)) {
    fireEvent.change(screen.getByRole("textbox", { name: label }), { target: { value } });
  }
}

it("starts a custom API with blank API-specific fields and does not restore cleared mappings", () => {
  renderAdmin(<PartnerConnectionForm providers={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
  for (const label of [
    "API base URL",
    "Products endpoint",
    "Product detail endpoint",
    "Product list paths",
    "Detail response root",
    "Listing URL template",
    ...Object.keys(customMappingPaths),
    "Platform hosts",
    "Attribution",
    "Page size parameter",
  ]) {
    expect((screen.getByRole("textbox", { name: label }) as HTMLInputElement).value).toBe("");
  }
  const id = screen.getByRole("textbox", { name: "Product ID paths" }) as HTMLInputElement;
  fireEvent.change(id, { target: { value: "metadata.id" } });
  fireEvent.change(id, { target: { value: "" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "My custom API" },
  });
  expect(id.value).toBe("");
  expect(api.adminPartnerConnectorResponse).not.toHaveBeenCalled();
});

it.each([provider, shipyardProvider])(
  "clears the $name preset's API mapping and pagination values when switching to Custom API",
  (preset) => {
    renderAdmin(
      <PartnerConnectionForm providers={[preset]} onSaved={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(
      (screen.getByRole("textbox", { name: "Products endpoint" }) as HTMLInputElement).value,
    ).not.toBe("");
    fireEvent.click(screen.getByRole("combobox", { name: "Platform" }));
    fireEvent.click(screen.getByRole("option", { name: "Custom API" }));
    for (const label of [
      "API base URL",
      "Products endpoint",
      "Product list paths",
      ...Object.keys(customMappingPaths),
      "Page size parameter",
    ]) {
      expect((screen.getByRole("textbox", { name: label }) as HTMLInputElement).value).toBe("");
    }
    fireEvent.click(screen.getByRole("combobox", { name: "Pagination mode" }));
    fireEvent.click(screen.getByRole("option", { name: "Cursor" }));
    expect(
      (screen.getByRole("textbox", { name: "Pagination parameter" }) as HTMLInputElement).value,
    ).toBe("");
    expect(
      (screen.getByRole("textbox", { name: "Next token path" }) as HTMLInputElement).value,
    ).toBe("");
  },
);

it("selects the Shipyard preset with official website and platform listing URL roles", async () => {
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue({
    ...connection,
    provider: "shipyardhq",
  });
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider, shipyardProvider]}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Platform" }));
  fireEvent.click(screen.getByRole("option", { name: "ShipyardHQ" }));
  expect((screen.getByLabelText(/^API base URL/) as HTMLInputElement).value).toBe(
    "https://shipyardhq.dev",
  );
  expect((screen.getByLabelText("Website URL paths") as HTMLInputElement).value).toBe("productUrl");
  expect((screen.getByLabelText("Listing URL paths") as HTMLInputElement).value).toBe("url");
  expect(screen.queryByRole("textbox", { name: "Secret reference" })).toBeNull();
  const token = screen.getByLabelText(/^Bearer token/) as HTMLInputElement;
  expect(token.type).toBe("password");
  expect(token.value).toBe("");
  await selectAccount();
  fireEvent.change(token, { target: { value: "fixture-db-credential" } });
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  await waitFor(() => expect(api.adminPartnerConnectionCreate).toHaveBeenCalled());
  const payload = vi.mocked(api.adminPartnerConnectionCreate).mock.calls[0][0];
  expect(payload).toMatchObject({
    provider: "shipyardhq",
    name: "ShipyardHQ",
    credential: "fixture-db-credential",
    connector: {
      base_url: "https://shipyardhq.dev",
      list_path: "/api/products",
      detail_path: null,
      items_paths: ["items"],
      fields: {
        external_id: ["id"],
        product_url: ["productUrl"],
        listing_url: ["url"],
        pricing: ["pricing.model"],
      },
      auth: { mode: "bearer" },
      pagination: { mode: "cursor", next_path: "nextCursor", page_size: 10 },
      platform_hosts: ["shipyardhq.dev", "www.shipyardhq.dev"],
      filters: [],
      attribution: "Via ShipyardHQ",
    },
  });
});

it("preserves an explicitly saved Shipyard connector when editing the connection", async () => {
  const custom = {
    ...connectorDefaults("shipyardhq"),
    base_url: "https://custom.shipyard.example",
    list_path: "/custom-products",
    fields: {
      ...connectorDefaults("shipyardhq").fields,
      product_url: ["custom.homepage"],
      listing_url: ["custom.listing"],
    },
  };
  renderAdmin(
    <PartnerConnectionForm
      providers={[shipyardProvider]}
      connection={{
        ...connection,
        provider: "shipyardhq",
        connector: custom,
        credential_configured: true,
      }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  expect((screen.getByLabelText("Website URL paths") as HTMLInputElement).value).toBe(
    "custom.homepage",
  );
  expect((screen.getByLabelText("Listing URL paths") as HTMLInputElement).value).toBe(
    "custom.listing",
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      "shipyardhq",
      expect.objectContaining({ connector: custom, expected_revision: connection.revision }),
      expect.anything(),
    ),
  );
});

it("keeps stored admin credentials write-only and previews the saved connection revision", async () => {
  const saved = {
    ...connection,
    provider: "shipyardhq",
    connector: connectorDefaults("shipyardhq"),
    credential_configured: true,
  };
  vi.mocked(api.adminPartnerConnectorPreview).mockResolvedValue({
    discovered: 0,
    products: [],
    errors: [],
    has_next_page: false,
  });
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue(saved);
  renderAdmin(
    <PartnerConnectionForm
      providers={[shipyardProvider]}
      connection={saved}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText(/^Bearer token/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectorPreview).toHaveBeenCalledWith(
      { provider: saved.provider, connector: saved.connector, expected_revision: saved.revision },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Replace credential" }));
  const token = screen.getByLabelText(/^Bearer token/) as HTMLInputElement;
  expect(token.value).toBe("");
  expect(token.type).toBe("password");
  fireEvent.change(token, { target: { value: "replacement-db-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectorPreview).toHaveBeenLastCalledWith(
      expect.objectContaining({
        credential: "replacement-db-secret",
        expected_revision: saved.revision,
      }),
      expect.anything(),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      saved.provider,
      expect.objectContaining({
        credential: "replacement-db-secret",
        expected_revision: saved.revision,
      }),
      expect.anything(),
    ),
  );
});

it("requires replacing admin credentials when an authenticated API origin changes", () => {
  renderAdmin(
    <PartnerConnectionForm
      providers={[shipyardProvider]}
      connection={{
        ...connection,
        provider: "shipyardhq",
        connector: connectorDefaults("shipyardhq"),
        credential_configured: true,
      }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://new-catalog.example" },
  });
  expect((screen.getByLabelText(/^Bearer token/) as HTMLInputElement).value).toBe("");
  expect(
    (screen.getByRole("button", { name: "Test import connection" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.submit(screen.getByRole("form", { name: "Import connection settings" }));
  expect(api.adminPartnerConnectionUpdate).not.toHaveBeenCalled();
  expect(
    screen.getAllByText("Enter the credential for this authentication method.").length,
  ).toBeGreaterThan(0);
});

it("preserves an unchanged admin credential without storage and clears it through no authentication", async () => {
  const saved = {
    ...connection,
    provider: "shipyardhq",
    connector: connectorDefaults("shipyardhq"),
    credential_configured: true,
    credentials_available: false,
  };
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue(saved);
  renderAdmin(
    <PartnerConnectionForm
      providers={[shipyardProvider]}
      connection={saved}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.adminPartnerConnectionUpdate).toHaveBeenCalled());
  expect(vi.mocked(api.adminPartnerConnectionUpdate).mock.calls[0][1]).not.toHaveProperty(
    "credential",
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Authentication method" }));
  fireEvent.click(screen.getByRole("option", { name: "No authentication" }));
  expect(screen.getByText(/Saving without authentication/)).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.adminPartnerConnectionUpdate).mock.calls[1][1]).toMatchObject({
    connector: { auth: { mode: "none" } },
  });
});

it("uses an import connection table and canonical create page without manual product input", async () => {
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 25,
  });
  renderAdmin(<PartnerConnections />);
  expect(
    await screen.findByText(
      "No import connections. Create an import connection to configure a product API.",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("link", { name: "Create import connection" }).getAttribute("href")).toBe(
    "/partnerships/connections/new",
  );
  expect(
    screen.queryByRole("button", { name: /Add product|Import|Approve|Edit product/ }),
  ).toBeNull();
});

it.each(["new", "legacy"])(
  "prevents saving a %s connection without an account and focuses its field error",
  async (mode) => {
    renderAdmin(
      <PartnerConnectionForm
        providers={[provider]}
        connection={mode === "legacy" ? { ...connection, account_id: null } : undefined}
        onSaved={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.submit(screen.getByRole("form", { name: "Import connection settings" }));
    expect((await screen.findAllByText("Select a partner account.")).length).toBeGreaterThan(0);
    const picker = screen.getByRole("combobox", { name: "Partner account" });
    expect(picker.getAttribute("aria-invalid")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(picker));
    expect(api.adminPartnerConnectionCreate).not.toHaveBeenCalled();
    expect(api.adminPartnerConnectionUpdate).not.toHaveBeenCalled();
  },
);

it("searches and selects a required account before creating a disabled partner with CSRF", async () => {
  const saved = vi.fn();
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue(connection);
  renderAdmin(<PartnerConnectionForm providers={[provider]} onSaved={saved} onCancel={vi.fn()} />);
  const picker = screen.getByRole("combobox", { name: "Partner account" });
  expect(picker.getAttribute("aria-required")).toBe("true");
  fireEvent.click(picker);
  await screen.findByRole("option", { name: new RegExp(account.name) });
  expect(screen.queryByRole("option", { name: "None" })).toBeNull();
  fireEvent.change(screen.getByPlaceholderText("Search partner accounts…"), {
    target: { value: "Example" },
  });
  await waitFor(() =>
    expect(api.accountsV1AdminPartnerAccountsGet).toHaveBeenCalledWith(
      { q: "Example", offset: 0, limit: 25 },
      { signal: expect.any(AbortSignal) },
    ),
  );
  fireEvent.click(await screen.findByRole("option", { name: new RegExp(account.name) }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionCreate).toHaveBeenCalledWith(
      {
        provider: "nick-launches",
        name: "Nick Launches",
        connector: connectorDefaults("nick-launches"),
        account_id: account.id,
        enabled: false,
        sync_interval_minutes: 360,
      },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
  await waitFor(() => expect(saved).toHaveBeenCalledWith(connection));
});

it("edits enabled with revision protection and keeps provider fixed", async () => {
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({
    ...connection,
    enabled: true,
    revision: 2,
  });
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  expect(screen.getByRole("combobox", { name: /Platform/ }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      "nick-launches",
      {
        account_id: account.id,
        name: "Nick Launches",
        connector: connectorDefaults("nick-launches"),
        enabled: true,
        expected_revision: 1,
        sync_interval_minutes: 360,
      },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("imports and removes logo URLs immediately without saving unrelated draft fields", async () => {
  const stored = { ...connection, logo_url: "https://cdn.example/old.webp" };
  const imported = {
    ...stored,
    revision: 2,
    logo_url: "https://cdn.example/new.webp",
    logo_variants: [{ url: "https://cdn.example/new.webp", width: 64 }],
  };
  vi.mocked(api.adminPartnerConnectionLogoUpdate)
    .mockResolvedValueOnce(imported)
    .mockResolvedValueOnce({ ...stored, revision: 3, logo_url: null, logo_variants: [] });
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({ ...stored, revision: 4 });
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={stored}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Unsaved platform name" },
  });
  const input = screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://remote.example/new.png" } });
  fireEvent.blur(input);
  await waitFor(() => expect(input.value).toBe(imported.logo_url));
  expect(api.adminPartnerConnectionUpdate).not.toHaveBeenCalled();
  expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(
    "Unsaved platform name",
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionLogoUpdate).toHaveBeenLastCalledWith(
      connection.provider,
      { logo_url: null, expected_revision: 2 },
      expect.anything(),
    ),
  );
  await screen.findByText("Logo removed.");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.adminPartnerConnectionUpdate).toHaveBeenCalled());
  const payload = vi.mocked(api.adminPartnerConnectionUpdate).mock.calls[0][1];
  expect(payload).toMatchObject({ name: "Unsaved platform name", expected_revision: 3 });
  expect(payload).not.toHaveProperty("logo_url");
});

it("saves a newly typed logo URL and other draft settings on the first Save click", async () => {
  const user = userEvent.setup();
  const saved = vi.fn();
  const result = {
    ...connection,
    name: "Edited name",
    revision: 2,
    logo_url: "https://cdn.example/saved.webp",
    logo_variants: [{ url: "https://cdn.example/saved.webp", width: 64 }],
  };
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue(result);
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={saved}
      onCancel={vi.fn()}
    />,
  );
  const name = screen.getByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "Edited name");
  const logo = screen.getByRole("textbox", { name: "Logo URL" });
  await user.type(logo, "https://remote.example/logo.png");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(saved).toHaveBeenCalledExactlyOnceWith(result));
  expect(api.adminPartnerConnectionLogoUpdate).not.toHaveBeenCalled();
  expect(api.adminPartnerConnectionLogoUpload).not.toHaveBeenCalled();
  expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledExactlyOnceWith(
    connection.provider,
    expect.objectContaining({
      name: "Edited name",
      logo_url: "https://remote.example/logo.png",
      expected_revision: 1,
    }),
    expect.anything(),
  );
});

it("uploads a saved connection logo immediately and keeps a separate name edit dirty", async () => {
  const uploaded = {
    ...connection,
    revision: 2,
    logo_url: "https://cdn.example/upload.webp",
    logo_variants: [{ url: "https://cdn.example/upload.webp", width: 64 }],
  };
  vi.mocked(api.adminPartnerConnectionLogoUpload).mockResolvedValue(uploaded);
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({ ...uploaded, revision: 3 });
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Draft name" },
  });
  const file = new File(["image"], "platform.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  await screen.findByText("Logo updated.");
  expect((screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement).value).toBe(
    uploaded.logo_url,
  );
  expect((screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value).toBe(
    "Draft name",
  );
  expect(api.adminPartnerConnectionUpdate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.adminPartnerConnectionUpdate).toHaveBeenCalled());
  expect(vi.mocked(api.adminPartnerConnectionUpdate).mock.calls[0][1]).toMatchObject({
    name: "Draft name",
    expected_revision: 2,
  });
  expect(vi.mocked(api.adminPartnerConnectionUpdate).mock.calls[0][1]).not.toHaveProperty(
    "logo_url",
  );
});

it("blocks invalid logo submissions and allows saving again after removal", async () => {
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue(connection);
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("Logo image file"), {
    target: { files: [new File(["<svg/>"], "invalid.svg", { type: "image/svg+xml" })] },
  });
  const submit = screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.submit(submit.closest("form")!);
  expect(api.adminPartnerConnectionUpdate).not.toHaveBeenCalled();
  expect(api.adminPartnerConnectionLogoUpload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledOnce());
});

it("keeps a newly created connection and retries its upload without creating a duplicate", async () => {
  const saved = vi.fn();
  const created = { ...connection, enabled: true, revision: 2 };
  const uploaded = {
    ...created,
    revision: 4,
    logo_url: "https://cdn.example/platform.webp",
    logo_variants: [{ url: "https://cdn.example/platform.webp", width: 64 }],
  };
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue(created);
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({ ...created, revision: 3 });
  vi.mocked(api.adminPartnerConnectionLogoUpload)
    .mockRejectedValueOnce(new Error("Storage temporarily unavailable"))
    .mockResolvedValueOnce(uploaded);
  renderAdmin(<PartnerConnectionForm providers={[provider]} onSaved={saved} onCancel={vi.fn()} />);
  await selectAccount();
  const file = new File(["image"], "platform.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Connection settings saved, but the logo was not uploaded",
  );
  expect(saved).not.toHaveBeenCalled();
  expect(screen.getByText("platform.png")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(uploaded));
  expect(api.adminPartnerConnectionCreate).toHaveBeenCalledOnce();
  expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
    created.provider,
    expect.objectContaining({ expected_revision: 2 }),
    expect.anything(),
  );
  expect(api.adminPartnerConnectionLogoUpload).toHaveBeenNthCalledWith(
    1,
    created.provider,
    { file, expected_revision: 2 },
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  expect(api.adminPartnerConnectionLogoUpload).toHaveBeenNthCalledWith(
    2,
    created.provider,
    { file, expected_revision: 3 },
    expect.anything(),
  );
});

it("preserves form choices after an API failure and can cancel without saving", async () => {
  const cancel = vi.fn();
  vi.mocked(api.adminPartnerConnectionUpdate).mockRejectedValue(new Error("Settings changed"));
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={vi.fn()}
      onCancel={cancel}
    />,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect((screen.getByRole("checkbox", { name: "Enabled" }) as HTMLInputElement).checked).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(cancel).toHaveBeenCalledOnce();
  expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledOnce();
});

it("links existing partners to details and edit pages and allows another API connection", async () => {
  renderAdmin(<PartnerConnections />);
  expect((await screen.findByRole("link", { name: "Nick Launches" })).getAttribute("href")).toBe(
    "/partnerships/connections/nick-launches",
  );
  expect(screen.getByRole("link", { name: "Edit Nick Launches" }).getAttribute("href")).toBe(
    "/partnerships/connections/nick-launches/edit",
  );
  expect(
    screen.getByRole("link", { name: "Create import connection" }).getAttribute("aria-disabled"),
  ).toBe(null);
});

it("labels pipeline relationships as import connections and links their canonical routes", async () => {
  vi.mocked(api.adminPartnerPipelineList).mockResolvedValue({
    items: [
      {
        id: "run-12345678",
        provider: connection.provider,
        operation: "sync",
        status: "succeeded",
        attempts: 1,
        product_id: null,
        product_name: null,
        parent_id: null,
        external_id: null,
        created_at: "2026-10-11T00:00:00Z",
        available_at: "2026-10-11T00:00:00Z",
        finished_at: null,
        error: null,
      },
    ],
    total: 1,
    offset: 0,
    limit: 25,
  });
  renderAdmin(<PartnerRuns kind="pipeline" />);
  expect(
    (await screen.findByRole("link", { name: connection.provider })).getAttribute("href"),
  ).toBe("/partnerships/connections/nick-launches");
  expect(screen.getByRole("columnheader", { name: "Import connection" })).toBeTruthy();
  expect(screen.queryByRole("columnheader", { name: "Partner" })).toBeNull();
});

it("provides retry and exclusion for synced products without editing them", async () => {
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue({
    items: [{ ...connection, enabled: true, state: "idle", products: 1, needs_attention: 1 }],
    total: 1,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [product],
    total: 1,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerProductAction).mockResolvedValue({ ...product, excluded: true });
  renderAdmin(<PartnerProductPage id={product.id} />);
  expect(await screen.findByText("Product website could not be read")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Exclude product" }));
  await waitFor(() =>
    expect(api.adminPartnerProductAction).toHaveBeenCalledWith(
      product.id,
      { action: "exclude", expected_revision: 1 },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

const directProduct: ProductOut = {
  ...product,
  partnership_type: "direct",
  metadata_listing_id: null,
  listings: [],
  commercial_approved: true,
  approval_sources: ["trusted"],
  manual_sources: [
    {
      account_id: account.id,
      account_name: account.name,
      active: true,
      commercial_approved: true,
      approval_source: "trusted",
      updated_at: product.updated_at,
    },
  ],
};

it("lists direct account products with honest sources and supports revision-protected rechecks", async () => {
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [directProduct],
    total: 1,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerProductAction).mockResolvedValue(directProduct);
  const list = renderAdmin(<PartnerTools />);
  expect(await screen.findByText(`Direct · ${account.name}`)).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: "Sources" })).toBeTruthy();
  list.unmount();
  renderAdmin(<PartnerProductPage id={product.id} />);
  expect(await screen.findByText(/Accepted for associated partner accounts/)).toBeTruthy();
  expect(screen.getByText(/directly or imported it through a connection/)).toBeTruthy();
  expect(screen.getByRole("link", { name: `Direct · ${account.name}` })).toBeTruthy();
  const retry = screen.getByRole("button", { name: "Retry checks" }) as HTMLButtonElement;
  expect(retry.disabled).toBe(false);
  fireEvent.click(retry);
  await waitFor(() =>
    expect(api.adminPartnerProductAction).toHaveBeenCalledWith(
      product.id,
      { action: "retry", expected_revision: product.revision },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it.each([
  [false, "pending"],
  [true, "withdrawn"],
] as const)(
  "keeps retry disabled for direct source active=%s and status=%s",
  async (active, status) => {
    vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
      items: [
        {
          ...directProduct,
          status,
          manual_sources: [{ ...directProduct.manual_sources![0], active }],
        },
      ],
      total: 1,
      offset: 0,
      limit: 25,
    });
    renderAdmin(<PartnerProductPage id={product.id} />);
    const retry = (await screen.findByRole("button", {
      name: "Retry checks",
    })) as HTMLButtonElement;
    expect(retry.disabled).toBe(true);
    expect(api.adminPartnerProductAction).not.toHaveBeenCalled();
  },
);

it("shows direct account attribution separately from real platform listings", async () => {
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [directProduct],
    total: 1,
    offset: 0,
    limit: 25,
  });
  renderAdmin(<PartnerProductPage id={product.id} section="related" />);
  const sources = await screen.findByRole("region", { name: "Direct accounts" });
  expect(within(sources).getByRole("heading", { name: "Direct accounts (1)" })).toBeTruthy();
  expect(within(sources).getByRole("link", { name: account.name }).getAttribute("href")).toBe(
    `/partnerships/accounts/${account.id}`,
  );
  expect(within(sources).getByText("Active", { exact: true })).toBeTruthy();
  expect(within(sources).getByText("Accepted · trusted partner")).toBeTruthy();
  expect(within(sources).queryByText("Import connection")).toBeNull();
  expect(screen.queryByRole("region", { name: "Platform listings" })).toBeNull();
});

it("shows one product with independent platform listings and attribution", async () => {
  const sharedProduct: ProductOut = {
    ...product,
    listings: [
      product.listings[0],
      {
        ...product.listings[0],
        id: "55555555-5555-5555-5555-555555555555",
        provider: "platform-b",
        platform_name: "Platform B",
        attribution: "Via Platform B",
        listing_url: "https://platform-b.example/tools/checker",
        tagline: "Platform B describes the same product differently.",
      },
    ],
  };
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [sharedProduct],
    total: 1,
    offset: 0,
    limit: 25,
  });
  const list = renderAdmin(<PartnerTools />);
  expect((await screen.findAllByRole("link", { name: "API Checker" })).length).toBe(1);
  expect(screen.getByRole("link", { name: "API Checker" }).getAttribute("href")).toBe(
    `/partnerships/products/${product.id}`,
  );
  expect(screen.getByText("Nick Launches, Platform B")).toBeTruthy();
  list.unmount();
  renderAdmin(<PartnerProductPage id={product.id} section="related" />);
  await screen.findByRole("link", { name: "View Nick Launches listing" });
  expect(
    within(screen.getByRole("region", { name: "Platform listings" })).getAllByText(
      "Import connection",
    ),
  ).toHaveLength(2);
  expect(screen.getByRole("link", { name: "Nick Launches" }).getAttribute("href")).toBe(
    "/partnerships/connections/nick-launches",
  );
  expect(
    screen.getByRole("link", { name: "View Nick Launches listing" }).getAttribute("href"),
  ).toBe(product.listings[0].listing_url);
  expect(screen.getByRole("link", { name: "View Platform B listing" }).getAttribute("href")).toBe(
    "https://platform-b.example/tools/checker",
  );
});

it("distinguishes trusted account acceptance from pending capability checks and unaccepted imports", async () => {
  const accepted = {
    ...product,
    commercial_approved: true,
    approval_sources: ["trusted" as const],
  };
  const waiting = {
    ...product,
    id: "22222222-2222-4222-8222-222222222222",
    name: "Waiting product",
    commercial_approved: false,
  };
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [accepted, waiting],
    total: 2,
    offset: 0,
    limit: 25,
  });
  const view = renderAdmin(<PartnerTools />);
  await screen.findByText("Accepted · trusted partner");
  const acceptedRow = screen.getByRole("link", { name: product.name }).closest("tr")!;
  const waitingRow = screen.getByRole("link", { name: waiting.name }).closest("tr")!;
  expect(within(acceptedRow).getByText("Attention")).toBeTruthy();
  expect(within(acceptedRow).queryByText("Awaiting acceptance")).toBeNull();
  expect(within(waitingRow).getByText("Awaiting acceptance")).toBeTruthy();
  view.unmount();
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [accepted],
    total: 1,
    offset: 0,
    limit: 25,
  });
  renderAdmin(<PartnerProductPage id={product.id} />);
  expect(await screen.findByText(/Accepted for associated partner accounts/)).toBeTruthy();
  expect(screen.getByText("Product website could not be read")).toBeTruthy();
});

it("saves a custom sync interval with the other partner settings", async () => {
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({
    ...connection,
    sync_interval_minutes: 90,
    revision: 2,
  });
  renderAdmin(
    <PartnerConnectionForm
      providers={[provider]}
      connection={connection}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("spinbutton", { name: /Sync interval/ }), {
    target: { value: "90" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      "nick-launches",
      {
        account_id: account.id,
        name: "Nick Launches",
        connector: connectorDefaults("nick-launches"),
        enabled: false,
        expected_revision: 1,
        sync_interval_minutes: 90,
      },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("creates a custom API and previews the current mapping without saving", async () => {
  const custom = {
    ...connection,
    provider: "shipyard",
    name: "Shipyard",
    connector: connectorDefaults(),
  };
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue(custom);
  vi.mocked(api.adminPartnerConnectorPreview).mockResolvedValue({
    discovered: 1,
    products: [
      {
        provider: "shipyard",
        external_id: "checker",
        name: "Mapped Checker",
        product_url: "https://checker.example/",
        listing_url: "https://shipyard.example/products/checker",
        tagline: "Check API compatibility before deployment.",
      },
    ],
    errors: [],
    has_next_page: false,
  });
  renderAdmin(<PartnerConnectionForm providers={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Shipyard" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Identifier" }), {
    target: { value: "shipyard" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://api.shipyard.example" },
  });
  configureCustomMapping();
  fireEvent.change(screen.getByRole("textbox", { name: "Product list paths" }), {
    target: { value: "data.products, items" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  expect(await screen.findByText("Mapped Checker")).toBeTruthy();
  expect(api.adminPartnerConnectionCreate).not.toHaveBeenCalled();
  expect(api.adminPartnerConnectorPreview).toHaveBeenCalledWith(
    expect.objectContaining({
      provider: "shipyard",
      connector: expect.objectContaining({
        base_url: "https://api.shipyard.example",
        list_path: "/v4/products",
        items_paths: ["data.products", "items"],
        fields: {
          external_id: ["id"],
          name: ["name"],
          product_url: ["url"],
          listing_url: ["listing_url"],
          tagline: ["description"],
          pricing: ["pricing"],
        },
      }),
    }),
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  await selectAccount();
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        provider: "shipyard",
        name: "Shipyard",
        enabled: false,
        connector: expect.objectContaining({
          base_url: "https://api.shipyard.example",
          items_paths: ["data.products", "items"],
        }),
      }),
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

it("keeps saved custom connector settings when changing only the sync interval", async () => {
  const config = {
    ...connectorDefaults(),
    base_url: "https://api.shipyard.example",
    list_path: "/v4/products",
    items_paths: ["payload.products"],
    fields: {
      external_id: ["metadata.key"],
      name: ["title"],
      product_url: ["links.website"],
      listing_url: ["links.listing"],
      tagline: ["tagline"],
      pricing: ["plan"],
    },
    pagination: {
      mode: "offset" as const,
      parameter: "offset",
      size_parameter: "take",
      page_size: 40,
    },
    auth: { mode: "bearer" as const },
  };
  vi.mocked(api.adminPartnerConnectionUpdate).mockResolvedValue({
    ...connection,
    provider: "shipyard",
    name: "Shipyard",
    connector: config,
  });
  renderAdmin(
    <PartnerConnectionForm
      providers={[]}
      connection={{
        ...connection,
        provider: "shipyard",
        name: "Shipyard",
        connector: config,
        credential_configured: true,
      }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  for (const [label, value] of [
    ["Products endpoint", "/v4/products"],
    ["Product list paths", "payload.products"],
    ["Product ID paths", "metadata.key"],
    ["Product name paths", "title"],
    ["Website URL paths", "links.website"],
    ["Listing URL paths", "links.listing"],
    ["Tagline paths", "tagline"],
    ["Pricing paths", "plan"],
    ["Page size parameter", "take"],
  ]) {
    expect((screen.getByRole("textbox", { name: label }) as HTMLInputElement).value).toBe(value);
  }
  fireEvent.change(screen.getByRole("spinbutton", { name: "Sync interval (minutes)" }), {
    target: { value: "60" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      "shipyard",
      expect.objectContaining({
        connector: config,
        sync_interval_minutes: 60,
        expected_revision: 1,
      }),
      expect.anything(),
    ),
  );
});

it("preserves explicit pagination parameters across mode changes and leaves cleared values blank", async () => {
  const config = {
    ...connectorDefaults(),
    base_url: "https://api.example.com",
    pagination: { mode: "page" as const, parameter: "page_token", page_size: 10 },
  };
  renderAdmin(
    <PartnerConnectionForm
      providers={[]}
      connection={{ ...connection, provider: "custom-api", connector: config }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  const list = screen.getByRole("textbox", { name: "Product list paths" }) as HTMLInputElement;
  fireEvent.change(list, { target: { value: "items," } });
  expect(list.value).toBe("items,");
  fireEvent.change(list, { target: { value: "items, data.products" } });
  expect(list.value).toBe("items, data.products");
  fireEvent.click(screen.getByRole("combobox", { name: "Pagination mode" }));
  fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Offset" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("textbox", { name: "Pagination parameter" }) as HTMLInputElement).value,
    ).toBe("page_token"),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Pagination parameter" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("combobox", { name: "Pagination mode" }));
  fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: "Cursor" }));
  expect(
    (screen.getByRole("textbox", { name: "Pagination parameter" }) as HTMLInputElement).value,
  ).toBe("");
});

it("highlights nested connector errors, opens their section and focuses the first invalid field", async () => {
  vi.mocked(api.adminPartnerConnectionCreate).mockRejectedValue(
    new ApiError(422, "Please correct the highlighted fields.", {
      "connector.fields.name.0": "Use a valid response path.",
      "connector.max_pages": "Must be at most 1000.",
      provider: "Choose a valid identifier.",
    }),
  );
  renderAdmin(<PartnerConnectionForm providers={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Identifier" }), {
    target: { value: "custom" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Custom API" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://api.example.com" },
  });
  configureCustomMapping();
  await selectAccount();
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  await waitFor(() =>
    expect(
      screen.getByRole("textbox", { name: "Product name paths" }).getAttribute("aria-invalid"),
    ).toBe("true"),
  );
  expect(screen.getByRole("textbox", { name: "Identifier" }).getAttribute("aria-invalid")).toBe(
    "true",
  );
  await waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Identifier" })),
  );
  const pages = screen.getByRole("spinbutton", { name: "Maximum pages" });
  expect(pages.getAttribute("aria-invalid")).toBe("true");
  expect(pages.closest("details")?.open).toBe(true);
  expect(screen.getAllByText("Must be at most 1000.").length).toBeGreaterThan(0);
});

it.each(["preview", "save"])(
  "submits cleared custom mapping arrays explicitly for %s and displays field validation",
  async (action) => {
    const validation = new ApiError(422, "Please correct the highlighted fields.", {
      "connector.fields.external_id": "Configure the product ID mapping.",
    });
    vi.mocked(api.adminPartnerConnectorPreview).mockRejectedValue(validation);
    vi.mocked(api.adminPartnerConnectionCreate).mockRejectedValue(validation);
    renderAdmin(<PartnerConnectionForm providers={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Identifier" }), {
      target: { value: "custom" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "Custom API" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
      target: { value: "https://api.example.com" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Products endpoint" }), {
      target: { value: "/catalog" },
    });
    const id = screen.getByRole("textbox", { name: "Product ID paths" }) as HTMLInputElement;
    fireEvent.change(id, { target: { value: "id" } });
    fireEvent.change(id, { target: { value: "" } });
    await selectAccount();
    fireEvent.click(
      screen.getByRole("button", {
        name: action === "preview" ? "Test import connection" : "Create import connection",
      }),
    );
    await screen.findAllByText("Configure the product ID mapping.");
    const request =
      action === "preview"
        ? vi.mocked(api.adminPartnerConnectorPreview).mock.calls[0]
        : vi.mocked(api.adminPartnerConnectionCreate).mock.calls[0];
    expect(request[0].connector).toMatchObject({
      list_path: "/catalog",
      items_paths: [""],
      fields: {
        external_id: [],
        name: [],
        product_url: [],
        listing_url: [],
        tagline: [],
        pricing: [],
      },
      pagination: { mode: "none", parameter: null, size_parameter: null },
    });
    expect(request[1]).toEqual({ headers: { "X-CSRF-Token": "test-csrf" } });
    expect(id.value).toBe("");
    expect(id.getAttribute("aria-invalid")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(id));
  },
);

it("offers no empty connector choices while preserving explicit no-pagination and no-auth modes", async () => {
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue(connection);
  renderAdmin(
    <PartnerConnectionForm providers={[provider]} onSaved={vi.fn()} onCancel={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Platform" }));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    "Nick Launches",
    "Custom API",
  ]);
  expect(screen.queryByRole("option", { name: "None" })).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "Custom API" }));
  for (const setting of [
    { label: "Pagination mode", enabled: "Cursor", off: "None" },
    { label: "Authentication method", enabled: "Bearer token", off: "No authentication" },
  ]) {
    const trigger = screen.getByRole("combobox", { name: setting.label });
    expect(trigger.getAttribute("aria-required")).toBe("true");
    fireEvent.click(trigger);
    expect(screen.getAllByRole("option", { name: setting.off })).toHaveLength(1);
    fireEvent.click(screen.getByRole("option", { name: setting.enabled }));
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("option", { name: setting.off }));
    expect(trigger.textContent).toBe(setting.off);
  }
  await selectAccount();
  fireEvent.change(screen.getByRole("textbox", { name: "Identifier" }), {
    target: { value: "public-catalog" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Public catalog" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "API base URL" }), {
    target: { value: "https://catalog.example" },
  });
  configureCustomMapping();
  fireEvent.click(screen.getByRole("button", { name: "Create import connection" }));
  await waitFor(() => expect(api.adminPartnerConnectionCreate).toHaveBeenCalled());
  expect(vi.mocked(api.adminPartnerConnectionCreate).mock.calls[0][0].connector).toMatchObject({
    pagination: { mode: "none" },
    auth: { mode: "none" },
  });
});

it.each(["pipeline", "evaluations"] as const)(
  "offers explicit all-status and all-operation filters without an empty None in %s",
  (kind) => {
    renderAdmin(<PartnerRuns kind={kind} />);
    const status = screen.getByRole("combobox", { name: "Status" });
    expect(status.getAttribute("aria-required")).toBe("true");
    fireEvent.click(status);
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "All statuses",
      "Queued",
      "Running",
      "Succeeded",
      "Failed",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "Failed" }));
    expect(replace).toHaveBeenLastCalledWith(
      `/partnerships/${kind}?limit=25&offset=0&status=failed`,
      { scroll: false },
    );
    fireEvent.click(status);
    fireEvent.click(screen.getByRole("option", { name: "All statuses" }));
    expect(replace).toHaveBeenLastCalledWith(`/partnerships/${kind}?limit=25&offset=0`, {
      scroll: false,
    });
    if (kind === "pipeline") {
      const operation = screen.getByRole("combobox", { name: "Operation" });
      expect(operation.getAttribute("aria-required")).toBe("true");
      fireEvent.click(operation);
      expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
        "All operations",
        "Discover products",
        "Sync product",
        "Product check",
      ]);
      fireEvent.click(screen.getByRole("option", { name: "Product check" }));
      expect(replace).toHaveBeenLastCalledWith(
        "/partnerships/pipeline?limit=25&offset=0&operation=assess",
        { scroll: false },
      );
      fireEvent.click(operation);
      fireEvent.click(screen.getByRole("option", { name: "All operations" }));
      expect(replace).toHaveBeenLastCalledWith("/partnerships/pipeline?limit=25&offset=0", {
        scroll: false,
      });
    } else {
      expect(screen.queryByRole("combobox", { name: "Operation" })).toBeNull();
    }
  },
);

it("shows preview validation errors against their fields", async () => {
  vi.mocked(api.adminPartnerConnectorPreview).mockRejectedValue(
    new ApiError(422, "Please correct the highlighted fields.", {
      "connector.base_url": "A public HTTPS origin is required.",
    }),
  );
  renderAdmin(
    <PartnerConnectionForm providers={[provider]} onSaved={vi.fn()} onCancel={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Test import connection" }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "API base URL" }).getAttribute("aria-invalid")).toBe(
      "true",
    ),
  );
  expect(screen.getAllByText("A public HTTPS origin is required.").length).toBeGreaterThan(0);
});

it("previews raw API fields independently of mapping changes", async () => {
  vi.mocked(api.adminPartnerConnectorResponse).mockResolvedValue({
    data: { results: [{ slug: "checker", title: "API Checker" }], nextCursor: null },
    sampled: true,
  });
  renderAdmin(
    <PartnerConnectionForm providers={[provider]} onSaved={vi.fn()} onCancel={vi.fn()} />,
  );
  const json = await screen.findByLabelText("API response JSON");
  expect(json.getAttribute("role")).toBe("textbox");
  expect(json.getAttribute("aria-readonly")).toBe("true");
  expect(json.textContent).toContain('"slug": "checker"');
  await waitFor(() =>
    expect(json.querySelector('[data-mappings="fields.external_id"]')).not.toBeNull(),
  );
  const id = screen.getByRole("textbox", { name: "Product ID paths" });
  expect(id.className).toContain("border-violet-600");
  expect(
    screen.getByLabelText("Product list found in sample").getAttribute("data-mapping-status"),
  ).toBe("matched");
  expect(json.querySelector('[data-mappings="fields.external_id"]')?.className).toContain(
    "border-violet-600",
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Product name paths" }), {
    target: { value: "title" },
  });
  await waitFor(() => expect(json.querySelector('[data-mappings="fields.name"]')).not.toBeNull());
  expect(api.adminPartnerConnectorResponse).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByRole("textbox", { name: "Product list paths" }), {
    target: { value: "unknown.path" },
  });
  expect(json.querySelector('[data-mappings="fields.external_id"]')).toBeNull();
  expect(id.className).not.toContain("border-violet-600");
  expect(api.adminPartnerConnectorResponse).toHaveBeenCalledOnce();
  expect(api.adminPartnerConnectorResponse).toHaveBeenCalledWith(
    expect.objectContaining({
      connector: expect.objectContaining({
        base_url: "https://nicklaunches.com",
        list_path: "/api/v1/products/",
      }),
    }),
    expect.objectContaining({ headers: { "X-CSRF-Token": "test-csrf" } }),
  );
});

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerConnectionForm } from "@/components/organisms/partner-connection-form";
import { PartnerConnections } from "@/components/organisms/partner-connections";
import { PartnerProductPage } from "@/components/organisms/partner-product-page";
import { PartnerTools } from "@/components/organisms/partner-tools";
import * as api from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { connectorDefaults } from "@/lib/partner-connectors";
import type { ConnectionOut, ProductOut } from "@/lib/api/generated/models";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("@/lib/api/generated/admin", () => ({
  adminPartnerToolsList: vi.fn(),
  adminPartnerConnectionsList: vi.fn(),
  adminPartnerProvidersList: vi.fn(),
  adminPartnerConnectionCreate: vi.fn(),
  adminPartnerConnectorPreview: vi.fn(),
  adminPartnerConnectionUpdate: vi.fn(),
  adminPartnerConnectionAction: vi.fn(),
  adminPartnerProductAction: vi.fn(),
  adminPartnerToolsEvaluations: vi.fn(),
  adminPartnerPipelineList: vi.fn(),
  adminPartnerEvaluationsList: vi.fn(),
}));
const connection: ConnectionOut = {
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
};
const product: ProductOut = {
  id: "11111111-1111-1111-1111-111111111111",
  partnership_type: "launch_platform",
  name: "API Checker",
  product_url: "https://checker.example/",
  description: "Detect breaking API changes before deployment.",
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
      description: "Check OpenAPI compatibility.",
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
  vi.mocked(api.adminPartnerProvidersList).mockResolvedValue([provider]);
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue([connection]);
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
};

it("uses a routed partner table and create page without manual product input", async () => {
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue([]);
  renderAdmin(<PartnerConnections />);
  expect(
    await screen.findByText("No partners. Create a partner to configure its API."),
  ).toBeTruthy();
  expect(screen.getByRole("link", { name: "Create partner" }).getAttribute("href")).toBe(
    "/partnerships/partners/new",
  );
  expect(
    screen.queryByRole("button", { name: /Add product|Import|Approve|Edit product/ }),
  ).toBeNull();
});

it("creates a disabled partner through the supported provider form", async () => {
  const saved = vi.fn();
  vi.mocked(api.adminPartnerConnectionCreate).mockResolvedValue(connection);
  renderAdmin(<PartnerConnectionForm providers={[provider]} onSaved={saved} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Create partner" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionCreate).toHaveBeenCalledWith(
      {
        provider: "nick-launches",
        name: "Nick Launches",
        connector: connectorDefaults(true),
        account_id: null,
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
  expect(screen.getByRole("combobox", { name: /Partner/ }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(api.adminPartnerConnectionUpdate).toHaveBeenCalledWith(
      "nick-launches",
      {
        account_id: null,
        name: "Nick Launches",
        connector: connectorDefaults(true),
        enabled: true,
        expected_revision: 1,
        sync_interval_minutes: 360,
      },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
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
    "/partnerships/partners/nick-launches",
  );
  expect(screen.getByRole("link", { name: "Edit Nick Launches" }).getAttribute("href")).toBe(
    "/partnerships/partners/nick-launches/edit",
  );
  expect(screen.getByRole("link", { name: "Create partner" }).getAttribute("aria-disabled")).toBe(
    null,
  );
});

it("provides retry and exclusion for synced products without editing them", async () => {
  vi.mocked(api.adminPartnerConnectionsList).mockResolvedValue([
    { ...connection, enabled: true, state: "idle", products: 1, needs_attention: 1 },
  ]);
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
        description: "Platform B describes the same product differently.",
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
    screen.getByRole("link", { name: "View Nick Launches listing" }).getAttribute("href"),
  ).toBe(product.listings[0].listing_url);
  expect(screen.getByRole("link", { name: "View Platform B listing" }).getAttribute("href")).toBe(
    "https://platform-b.example/tools/checker",
  );
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
        account_id: null,
        name: "Nick Launches",
        connector: connectorDefaults(true),
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
        description: "Check API compatibility before deployment.",
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
  fireEvent.change(screen.getByRole("textbox", { name: "Product list paths" }), {
    target: { value: "data.products, items" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  expect(await screen.findByText("Mapped Checker")).toBeTruthy();
  expect(api.adminPartnerConnectionCreate).not.toHaveBeenCalled();
  expect(api.adminPartnerConnectorPreview).toHaveBeenCalledWith(
    expect.objectContaining({
      provider: "shipyard",
      connector: expect.objectContaining({
        base_url: "https://api.shipyard.example",
        items_paths: ["data.products", "items"],
      }),
    }),
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Enabled" }));
  fireEvent.click(screen.getByRole("button", { name: "Create partner" }));
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
      expect.anything(),
    ),
  );
});

it("keeps saved custom connector settings when changing only the sync interval", async () => {
  const config = {
    ...connectorDefaults(),
    base_url: "https://api.shipyard.example",
    list_path: "/v4/products",
    pagination: { mode: "offset" as const, parameter: "offset", page_size: 40 },
    auth: { mode: "bearer" as const, secret_ref: "DEVFEED_PARTNER_SECRET_SHIPYARD" },
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
      connection={{ ...connection, provider: "shipyard", name: "Shipyard", connector: config }}
      onSaved={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
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

it("updates displayed pagination parameters when changing modes and preserves delimiter typing", async () => {
  const config = {
    ...connectorDefaults(),
    base_url: "https://api.example.com",
    pagination: { mode: "page" as const, parameter: "page", page_size: 10 },
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
    ).toBe("offset"),
  );
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
  fireEvent.click(screen.getByRole("button", { name: "Create partner" }));
  await waitFor(() =>
    expect(
      screen.getByRole("textbox", { name: "Product name paths" }).getAttribute("aria-invalid"),
    ).toBe("true"),
  );
  expect(screen.getByRole("textbox", { name: "Identifier" }).getAttribute("aria-invalid")).toBe(
    "true",
  );
  expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Identifier" }));
  const pages = screen.getByRole("spinbutton", { name: "Maximum pages" });
  expect(pages.getAttribute("aria-invalid")).toBe("true");
  expect(pages.closest("details")?.open).toBe(true);
  expect(screen.getAllByText("Must be at most 1000.").length).toBeGreaterThan(0);
});

it("does not offer an empty None choice for required connector settings", () => {
  renderAdmin(<PartnerConnectionForm providers={[]} onSaved={vi.fn()} onCancel={vi.fn()} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Pagination mode" }));
  expect(screen.getAllByRole("option", { name: /^None$/ }).length).toBe(1);
});

it("shows preview validation errors against their fields", async () => {
  vi.mocked(api.adminPartnerConnectorPreview).mockRejectedValue(
    new ApiError(422, "Please correct the highlighted fields.", {
      "connector.base_url": "A public HTTPS origin is required.",
    }),
  );
  renderAdmin(
    <PartnerConnectionForm providers={[provider]} onSaved={vi.fn()} onCancel={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "API base URL" }).getAttribute("aria-invalid")).toBe(
      "true",
    ),
  );
  expect(screen.getAllByText("A public HTTPS origin is required.").length).toBeGreaterThan(0);
});

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerConnectionForm } from "@/components/organisms/partner-connection-form";
import { PartnerConnections } from "@/components/organisms/partner-connections";
import { PartnerProductPage } from "@/components/organisms/partner-product-page";
import { PartnerTools } from "@/components/organisms/partner-tools";
import * as api from "@/lib/api/generated/admin";
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
    await screen.findByText("No partners. Create a partner to connect a supported platform."),
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
      { provider: "nick-launches", account_id: null, enabled: false, sync_interval_minutes: 360 },
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
      { account_id: null, enabled: true, expected_revision: 1, sync_interval_minutes: 360 },
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

it("links existing partners to details and edit pages and prevents duplicate creation", async () => {
  renderAdmin(<PartnerConnections />);
  expect((await screen.findByRole("link", { name: "Nick Launches" })).getAttribute("href")).toBe(
    "/partnerships/partners/nick-launches",
  );
  expect(screen.getByRole("link", { name: "Edit Nick Launches" }).getAttribute("href")).toBe(
    "/partnerships/partners/nick-launches/edit",
  );
  expect(screen.getByRole("link", { name: "Create partner" }).getAttribute("aria-disabled")).toBe(
    "true",
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
      { account_id: null, enabled: false, expected_revision: 1, sync_interval_minutes: 90 },
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
});

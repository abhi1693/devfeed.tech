// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderAdmin } from "./render-admin";
import { PartnerTools } from "@/components/organisms/partner-tools";
import * as api from "@/lib/api/generated/admin";
import type { ProductOut } from "@/lib/api/generated/models";

vi.mock("@/lib/api/generated/admin", () => ({
  adminPartnerToolsList: vi.fn(),
  adminPartnerToolsImport: vi.fn(),
  adminPartnerToolsImportNick: vi.fn(),
  adminPartnerToolsReview: vi.fn(),
  adminPartnerToolsEvaluate: vi.fn(),
  adminPartnerToolsEvaluations: vi.fn(),
  adminPartnerToolsMatchReview: vi.fn(),
}));
const product: ProductOut = {
  id: "11111111-1111-1111-1111-111111111111",
  provider: "nick-launches",
  external_id: "checker",
  name: "API Checker",
  product_url: "https://checker.example/",
  listing_url: "https://nicklaunches.com/products/checker/",
  description: "Detect breaking API changes before deployment.",
  technologies: ["OpenAPI"],
  evidence: [
    {
      url: "https://checker.example/docs",
      quote: "Detect breaking changes in OpenAPI specifications.",
      capability: "Verify API compatibility in CI.",
    },
  ],
  pricing: "free",
  attribution: "Via Nick Launches",
  status: "pending",
  revision: 1,
  verified_at: null,
  updated_at: "2026-10-02T00:00:00Z",
  reviews: [],
  eligible: false,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [product],
    total: 1,
    offset: 0,
    limit: 25,
  });
  vi.mocked(api.adminPartnerToolsEvaluations).mockResolvedValue([]);
});
afterEach(cleanup);

it("requires evidence review and rights before approval and keeps evaluation private", async () => {
  renderAdmin(<PartnerTools />);
  fireEvent.click(await screen.findByRole("button", { name: /API Checker/ }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true),
  );
  expect(
    screen.getByRole("button", { name: "Run private evaluation" }).hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.change(screen.getByLabelText(/Review note/), {
    target: { value: "Checked the documentation and usage permission." },
  });
  fireEvent.click(screen.getByLabelText(/I checked the capability/));
  expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true);
  fireEvent.click(screen.getByLabelText(/Permission to display/));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(false),
  );
  vi.mocked(api.adminPartnerToolsReview).mockResolvedValue({
    ...product,
    status: "approved",
    eligible: true,
  });
  vi.mocked(api.adminPartnerToolsList).mockResolvedValue({
    items: [{ ...product, status: "approved", eligible: true }],
    total: 1,
    offset: 0,
    limit: 25,
  });
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() =>
    expect(api.adminPartnerToolsReview).toHaveBeenCalledWith(
      product.id,
      expect.objectContaining({
        status: "approved",
        evidence_checked: true,
        display_rights_confirmed: true,
      }),
      { headers: { "X-CSRF-Token": "test-csrf" } },
    ),
  );
  expect(await screen.findByText(/Reader visibility remains off/)).toBeTruthy();
});

it("accepts Nick API results without importing into the article feed", async () => {
  renderAdmin(<PartnerTools />);
  await screen.findByRole("button", { name: /API Checker/ });
  vi.mocked(api.adminPartnerToolsImportNick).mockResolvedValue([product]);
  fireEvent.click(screen.getByText("Import a product collection"));
  const source = {
    slug: "checker",
    name: "API Checker",
    productUrl: product.product_url,
    url: product.listing_url,
    tagline: product.description,
  };
  fireEvent.change(screen.getByLabelText(/Product collection JSON/), {
    target: { value: JSON.stringify({ results: [source], nextCursor: null }) },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import for review" }));
  await waitFor(() =>
    expect(api.adminPartnerToolsImportNick).toHaveBeenCalledWith(
      { products: [source] },
      expect.anything(),
    ),
  );
  expect(api.adminPartnerToolsImport).not.toHaveBeenCalled();
});

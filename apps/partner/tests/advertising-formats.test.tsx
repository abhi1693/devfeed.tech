// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerAdFormats } from "../src/app/partner-ad-formats";
import { PartnerProductCatalog } from "../src/app/partner-product-catalog";
import { PartnerProductPlacements } from "../src/app/partner-product-placements";
import type { Account } from "../src/lib/types";
import { partnerAccount } from "./partner-fixtures";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account: Account = partnerAccount({
  id: "11111111-1111-4111-8111-111111111111",
  name: "Shipyard",
  tier: "silver",
  benefits: [],
  status: "active",
  ad_formats: ["feed"],
  allowed_ad_formats: ["feed", "homepage_sponsorship", "sponsored_content", "engagement"],
});
const product = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "CronWatch",
  description: "Monitor scheduled jobs.",
  image_url: null,
  logo_url: null,
  product_url: "https://cronwatch.example/",
  listing_url: "https://shipyard.example/cronwatch",
};
const policy = {
  account_id: account.id,
  product_id: product.id,
  excluded: true,
  ad_formats: null,
  promotion_surfaces: null,
};
const saved = vi.fn();
const unavailable = vi.fn();
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function formats(value = account) {
  return render(
    <PartnerAdFormats
      account={value}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
}
function catalog() {
  return render(
    <PartnerProductCatalog
      account={account}
      csrfToken="partner-csrf"
      onUnavailable={unavailable}
    />,
  );
}
it("saves automatic formats independently and permits disabling every automatic format", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json({ ...account, ad_formats: [] }));
  vi.stubGlobal("fetch", fetcher);
  formats();
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.queryByRole("checkbox", { name: /Sponsored content/ })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: /^Feed/ }));
  await userEvent.click(screen.getByRole("button", { name: "Save ad formats" }));
  await screen.findByText("Ad formats saved.");
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/preferences`,
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ ad_formats: [] }),
      headers: { "content-type": "application/json", "x-csrf-token": "partner-csrf" },
    }),
  );
  expect(saved).toHaveBeenCalledWith(expect.objectContaining({ ad_formats: [] }));
});
it("retains downgraded formats without exposing saveable controls", () => {
  vi.stubGlobal("fetch", vi.fn());
  formats({
    ...account,
    tier: "bronze",
    allowed_ad_formats: ["feed"],
    ad_formats: ["homepage_sponsorship"],
  });
  const choice = screen.getByRole("checkbox", {
    name: /^Homepage sponsorship/,
  }) as HTMLInputElement;
  expect(choice.checked).toBe(true);
  expect(choice.disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Save ad formats" })).toBeNull();
  expect(screen.getByText(/Saved choices are retained/)).toBeTruthy();
});
it("loads creative previews only on request and keeps them separate from destination settings", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json({ product }));
  vi.stubGlobal("fetch", fetcher);
  formats();
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("Creative preview"));
  const preview = await screen.findByRole("region", { name: "Creative preview" });
  await within(preview).findByText(product.name);
  expect(within(preview).getByText("Sponsored")).toBeTruthy();
  expect(within(preview).queryByText("Visit website")).toBeNull();
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/creative-preview`,
    expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
  );
});
it.each([401, 404])("clears unavailable accounts after an ad format save (%s)", async (status) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));
  formats();
  await userEvent.click(screen.getByRole("button", { name: "Save ad formats" }));
  await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
  expect(saved).not.toHaveBeenCalled();
  if (status === 401) expect(router.replace).toHaveBeenCalledWith("/login");
});
it("does not publish malformed format saves and permits retry", async () => {
  const fetcher = vi
    .fn()
    .mockImplementationOnce(() => Response.json({ ...account, ad_formats: ["newsletter"] }))
    .mockImplementationOnce(() => Response.json(account));
  vi.stubGlobal("fetch", fetcher);
  formats();
  await userEvent.click(screen.getByRole("button", { name: "Save ad formats" }));
  await screen.findByRole("alert");
  expect(saved).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Save ad formats" }));
  await screen.findByText("Ad formats saved.");
});
it("edits an excluded product through owned routes without changing exclusion", async () => {
  const fetcher = vi.fn().mockImplementation(() => Response.json(policy));
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPlacements
      accountId={account.id}
      productId={product.id}
      name={product.name}
      allowedFormats={account.allowed_ad_formats!}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Product formats" });
  expect(screen.getByText(/This product is excluded/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(fetcher).toHaveBeenLastCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}/promotion`,
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ ad_formats: null, promotion_surfaces: null }),
      headers: { "content-type": "application/json", "x-csrf-token": "partner-csrf" },
    }),
  );
});
it("rejects a product policy belonging to another account", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => Response.json({ ...policy, account_id: "another-account" })),
  );
  render(
    <PartnerProductPlacements
      accountId={account.id}
      productId={product.id}
      name={product.name}
      allowedFormats={account.allowed_ad_formats!}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Save product placements" })).toBeNull();
});
it("searches the full imported catalog remotely and resets pagination", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(() => Response.json({ items: [{ ...product, ...policy }], total: 60 }));
  vi.stubGlobal("fetch", fetcher);
  catalog();
  expect(fetcher).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Manage product placements" }));
  await screen.findByText(product.name);
  expect(screen.getByText("Excluded")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Next imported products" }));
  await waitFor(() =>
    expect(fetcher).toHaveBeenLastCalledWith(
      expect.stringContaining("offset=25"),
      expect.anything(),
    ),
  );
  await screen.findByText(product.name);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search imported products" }), {
    target: { value: "cron" },
  });
  await waitFor(() =>
    expect(fetcher).toHaveBeenLastCalledWith(
      `/api/v1/partner/accounts/${account.id}/inventory?q=cron&offset=0&limit=25`,
      expect.anything(),
    ),
  );
  await screen.findByText(product.name);
  expect(
    (screen.getByRole("button", { name: "Previous imported products" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it("retries failed catalog reads and aborts an in-flight read when closed", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockImplementationOnce(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetcher);
  catalog();
  await userEvent.click(screen.getByRole("button", { name: "Manage product placements" }));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "Retry imported products" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const signal = fetcher.mock.calls[1][1].signal as AbortSignal;
  expect(signal.aborted).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "Close product catalog" }));
  expect(signal.aborted).toBe(true);
});

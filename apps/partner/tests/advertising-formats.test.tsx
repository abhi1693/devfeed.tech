// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  return render(<PartnerProductCatalog onUnavailable={unavailable} />);
}
function placements(productId = product.id) {
  return render(
    <PartnerProductPlacements
      accountId={account.id}
      productId={productId}
      name={product.name}
      allowedFormats={account.allowed_ad_formats!}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
}
async function openProductPlacements() {
  await screen.findByRole("combobox", { name: "Product availability" });
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
  await screen.findByRole("combobox", { name: "Product formats" });
  expect(screen.getByText(/This product is excluded/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(fetcher).toHaveBeenLastCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}/promotion`,
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ excluded: true, ad_formats: null, promotion_surfaces: null }),
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
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Save product placements" })).toBeNull();
});
it("restores an excluded product only when its draft is saved", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url: string, init: RequestInit) =>
      Response.json(init.method === "PUT" ? { ...policy, excluded: false } : policy),
    );
  vi.stubGlobal("fetch", fetcher);
  placements();
  await openProductPlacements();
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Allow this product" }));
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect(fetcher.mock.calls.filter(([, init]) => init.method === "PUT")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(fetcher).toHaveBeenLastCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}/promotion`,
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ excluded: false, ad_formats: null, promotion_surfaces: null }),
    }),
  );
});
it.each([false, true])(
  "previews only the chosen product, rejecting a wrong-product response (%s)",
  async (wrongProduct) => {
    const fetcher = vi.fn().mockImplementation((url: string) =>
      Response.json(
        url.includes("creative-preview")
          ? {
              product: wrongProduct
                ? {
                    ...product,
                    id: "44444444-4444-4444-8444-444444444444",
                    name: "Other product",
                  }
                : product,
            }
          : policy,
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    placements();
    await openProductPlacements();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === "PUT")).toHaveLength(0);
    const preview = await screen.findByRole("region", { name: "Creative preview" });
    if (wrongProduct) {
      await within(preview).findByText("Could not load the creative preview.");
      expect(screen.queryByText("Other product")).toBeNull();
    } else await within(preview).findByText(product.name);
    expect(fetcher).toHaveBeenLastCalledWith(
      `/api/v1/partner/accounts/${account.id}/creative-preview?product_id=${product.id}`,
      expect.objectContaining({ signal: expect.any(AbortSignal), credentials: "same-origin" }),
    );
  },
);
it("explains a null preview without inventing creative for an excluded product", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((url: string) =>
        Response.json(url.includes("creative-preview") ? { product: null } : policy),
      ),
  );
  placements();
  await openProductPlacements();
  await screen.findByText(/This product is unavailable for preview/);
  expect(screen.queryByText("Sponsored")).toBeNull();
});
it.each([401, 403, 404])("handles lost product-preview access (%s)", async (status) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((url: string) =>
        url.includes("creative-preview") ? new Response(null, { status }) : Response.json(policy),
      ),
  );
  placements();
  await openProductPlacements();
  await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
  if (status !== 404) expect(router.replace).toHaveBeenCalledWith("/login");
  expect(saved).not.toHaveBeenCalled();
});
it("aborts an inline preview and ignores late responses after page navigation", async () => {
  let resolvePreview!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation((url: string) =>
    url.includes("creative-preview")
      ? new Promise<Response>((resolve) => {
          resolvePreview = resolve;
        })
      : Response.json(policy),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = placements();
  await openProductPlacements();
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const signal = fetcher.mock.calls[1][1].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => resolvePreview(new Response(null, { status: 401 })));
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("aborts a save and ignores its late unauthorized response after page navigation", async () => {
  let finishSave!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) =>
    init.method === "PUT"
      ? new Promise<Response>((resolve) => {
          finishSave = resolve;
        })
      : Response.json(policy),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = placements();
  await openProductPlacements();
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  const signal = fetcher.mock.calls[2][1].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finishSave(new Response(null, { status: 401 })));
  expect(saved).not.toHaveBeenCalled();
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});
it("searches the full imported catalog remotely and resets pagination", async () => {
  const fetcher = vi.fn().mockImplementation((url: string) =>
    Response.json({
      items: [
        {
          ...product,
          ...policy,
          account_name: account.name,
          globally_excluded: false,
          status: "approved",
          technologies: [],
          commercial_approved: true,
        },
      ],
      total: 60,
      limit: 25,
      offset: Number(new URL(url, "https://partner.example").searchParams.get("offset")),
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  catalog();
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
  fireEvent.change(screen.getByRole("searchbox", { name: "Search products" }), {
    target: { value: "cron" },
  });
  await waitFor(() =>
    expect(fetcher).toHaveBeenLastCalledWith(
      `/api/v1/partner/inventory?q=cron&offset=0&limit=25`,
      expect.anything(),
    ),
  );
  await screen.findByText(product.name);
  expect(
    (screen.getByRole("button", { name: "Previous imported products" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it("retries failed catalog reads and aborts an in-flight read after page navigation", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockImplementationOnce(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetcher);
  const view = catalog();
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "Retry imported products" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const signal = fetcher.mock.calls[1][1].signal as AbortSignal;
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
});

// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerPreferences } from "../src/app/partner-preferences";
import type { Account } from "../src/lib/types";
import { partnerAccount } from "./partner-fixtures";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account: Account = partnerAccount({
  id: "11111111-1111-4111-8111-111111111111",
  name: "Shipyard",
  tier: "gold",
  benefits: [],
  status: "active",
  effective_promotion_kind: "ad",
  promotion_kind_source: "tier",
  approval_mode: "trusted",
  outbound_destination: "product_url",
});
const saved = vi.fn();
const unavailable = vi.fn();
const product = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "Shipyard deploy",
  product_url: "https://shipyard.example/?utm_source=devfeed&utm_medium=product",
  listing_url: "https://catalog.example/shipyard?utm_source=devfeed&utm_medium=product",
};
function stubApi(
  write = vi.fn(),
  preview = vi.fn().mockImplementation(() => Response.json({ product })),
) {
  const fetcher = vi.fn((url: string, init?: RequestInit) =>
    url.endsWith("/destination-preview") ? preview(url, init) : write(url, init),
  );
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function mount(value: Account = account) {
  return render(
    <PartnerPreferences
      account={value}
      csrfToken="partner-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  stubApi();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows inherited sponsorship and trusted import policy without product-by-product type selection", async () => {
  mount();
  expect(screen.getByText("Sponsored · inherited from tier")).toBeTruthy();
  expect(screen.getByText("Trusted partner imports accepted automatically")).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Product click destination" })).toBeTruthy();
  expect(screen.queryByRole("combobox", { name: /promotion type/i })).toBeNull();
  expect(screen.getByText(/DevFeed attribution/)).toBeTruthy();
  await screen.findByRole("link", { name: product.product_url });
});

it("saves only the chosen destination with the partner CSRF token", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ ...account, outbound_destination: "listing_url" }));
  stubApi(fetcher);
  mount();
  await userEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  await userEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  await screen.findByRole("status");
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/preferences`,
    expect.objectContaining({
      method: "PUT",
      headers: { "content-type": "application/json", "x-csrf-token": "partner-csrf" },
      body: JSON.stringify({ outbound_destination: "listing_url" }),
      signal: expect.any(AbortSignal),
    }),
  );
  expect(saved).toHaveBeenCalledWith(
    expect.objectContaining({ outbound_destination: "listing_url" }),
  );
  expect(unavailable).not.toHaveBeenCalled();
});

it.each([401, 404])("clears account data when saving loses access (%s)", async (status) => {
  stubApi(vi.fn().mockResolvedValue(new Response(null, { status })));
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
  expect(saved).not.toHaveBeenCalled();
  if (status === 401) expect(router.replace).toHaveBeenCalledWith("/login");
  else expect(router.replace).not.toHaveBeenCalled();
});

it("retains editable settings after a permission error and permits retry", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 403 }))
    .mockResolvedValueOnce(Response.json(account));
  stubApi(fetcher);
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Refresh the page and try again",
  );
  expect(unavailable).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  await screen.findByRole("status");
  expect(saved).toHaveBeenCalledOnce();
});

it.each([
  { ...account, id: "22222222-2222-4222-8222-222222222222" },
  { ...account, outbound_destination: "javascript:invalid" },
])("does not publish an invalid or different account response", async (value) => {
  stubApi(vi.fn().mockResolvedValue(Response.json(value)));
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  await screen.findByRole("alert");
  expect(saved).not.toHaveBeenCalled();
});

it("aborts a pending save when the account settings unmount", async () => {
  const fetcher = vi.fn().mockImplementation(() => new Promise(() => {}));
  stubApi(fetcher);
  const view = mount();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
  expect(saved).not.toHaveBeenCalled();
});

it("requires one of the two destinations and previews each actual attributed URL without saving", async () => {
  const fetcher = stubApi();
  mount();
  const preview = screen.getByRole("region", { name: "Click destination preview" });
  expect(
    (await within(preview).findByRole("link", { name: product.product_url })).getAttribute("href"),
  ).toBe(product.product_url);
  expect(within(preview).getByText(product.product_url)).toBeTruthy();
  expect(within(preview).queryByText(product.name)).toBeNull();
  expect(preview.querySelector("img")).toBeNull();
  const selector = screen.getByRole("combobox", { name: "Product click destination" });
  expect(selector.getAttribute("aria-required")).toBe("true");
  await userEvent.click(selector);
  expect(screen.queryByRole("option", { name: "None" })).toBeNull();
  expect(screen.getAllByRole("option")).toHaveLength(2);
  await userEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  expect(
    within(preview).getByRole("link", { name: product.listing_url }).getAttribute("href"),
  ).toBe(product.listing_url);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/destination-preview`,
    expect.objectContaining({
      cache: "no-store",
      credentials: "same-origin",
      signal: expect.any(AbortSignal),
    }),
  );
  expect(saved).not.toHaveBeenCalled();
});

it("shows no link for a missing listing and no fabricated product for an empty account", async () => {
  stubApi(
    undefined,
    vi.fn().mockImplementation(() => Response.json({ product: { ...product, listing_url: null } })),
  );
  const view = mount({ ...account, outbound_destination: "listing_url" });
  await screen.findByText("This product has no available partner listing.");
  expect(screen.queryByRole("link")).toBeNull();
  view.unmount();
  stubApi(
    undefined,
    vi.fn().mockImplementation(() => Response.json({ product: null })),
  );
  mount();
  await screen.findByText("A preview will appear when this account has an accepted product.");
  expect(screen.queryByText(product.name)).toBeNull();
});

it("retries a failed preview without losing an unsaved destination", async () => {
  const preview = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockImplementation(() => Response.json({ product }));
  stubApi(undefined, preview);
  mount();
  await screen.findByRole("button", { name: "Retry preview" });
  await userEvent.click(screen.getByRole("combobox", { name: "Product click destination" }));
  await userEvent.click(screen.getByRole("option", { name: "Partner listing" }));
  await userEvent.click(screen.getByRole("button", { name: "Retry preview" }));
  expect(
    (await screen.findByRole("link", { name: product.listing_url })).getAttribute("href"),
  ).toBe(product.listing_url);
  expect(preview).toHaveBeenCalledTimes(2);
  expect(saved).not.toHaveBeenCalled();
});

it("aborts account previews and ignores late results when changing accounts", async () => {
  let resolveFirst!: (value: Response) => void;
  const second = {
    ...account,
    id: "22222222-2222-4222-8222-222222222222",
    outbound_destination: "listing_url" as const,
  };
  const nextProduct = {
    ...product,
    id: second.id,
    name: "Second account product",
    product_url: "https://second.example/?utm_source=devfeed",
    listing_url: "https://catalog.example/second?utm_source=devfeed",
  };
  const preview = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFirst = resolve;
        }),
    )
    .mockImplementation(() => Response.json({ product: nextProduct }));
  stubApi(undefined, preview);
  const view = mount();
  const firstSignal = preview.mock.calls[0][1].signal as AbortSignal;
  view.rerender(
    <PartnerPreferences
      account={second}
      csrfToken="next-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
  expect(firstSignal.aborted).toBe(true);
  expect(
    (await screen.findByRole("link", { name: nextProduct.listing_url })).getAttribute("href"),
  ).toBe(nextProduct.listing_url);
  await act(async () => resolveFirst(Response.json({ product })));
  expect(screen.queryByRole("link", { name: product.product_url })).toBeNull();
  view.unmount();
});

it("keeps the preview request across parent rerenders and uses the current access callback", async () => {
  let resolvePreview!: (value: Response) => void;
  const preview = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        resolvePreview = resolve;
      }),
  );
  const currentUnavailable = vi.fn();
  stubApi(undefined, preview);
  const view = mount();
  view.rerender(
    <PartnerPreferences
      account={{ ...account }}
      csrfToken="new-csrf"
      onSaved={saved}
      onUnavailable={currentUnavailable}
    />,
  );
  expect(preview).toHaveBeenCalledTimes(1);
  await act(async () => resolvePreview(new Response(null, { status: 403 })));
  expect(currentUnavailable).toHaveBeenCalledOnce();
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).toHaveBeenCalledWith("/login");
});

it("ignores late access errors from a save for the previous account", async () => {
  let resolveSave!: (value: Response) => void;
  const write = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        resolveSave = resolve;
      }),
  );
  stubApi(write);
  const view = mount();
  await userEvent.click(screen.getByRole("button", { name: "Save click destination" }));
  view.rerender(
    <PartnerPreferences
      account={{ ...account, id: "22222222-2222-4222-8222-222222222222" }}
      csrfToken="new-csrf"
      onSaved={saved}
      onUnavailable={unavailable}
    />,
  );
  expect((write.mock.calls[0][1]!.signal as AbortSignal).aborted).toBe(true);
  await act(async () => resolveSave(new Response(null, { status: 401 })));
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
});

it.each([401, 403, 404])(
  "clears unavailable account data when preview access is lost (%s)",
  async (status) => {
    stubApi(
      undefined,
      vi.fn().mockImplementation(() => new Response(null, { status })),
    );
    mount();
    await waitFor(() => expect(unavailable).toHaveBeenCalledOnce());
    expect(screen.queryByText(product.name)).toBeNull();
    if (status === 401 || status === 403) expect(router.replace).toHaveBeenCalledWith("/login");
    else expect(router.replace).not.toHaveBeenCalled();
  },
);

it("treats malformed preview data as an error without displaying an invented destination", async () => {
  stubApi(
    undefined,
    vi.fn().mockImplementation(() => Response.json({ product: { name: product.name } })),
  );
  mount();
  await screen.findByRole("button", { name: "Retry preview" });
  expect(screen.queryByText(product.name)).toBeNull();
});

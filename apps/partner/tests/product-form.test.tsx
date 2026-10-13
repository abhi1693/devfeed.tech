// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerProductCreate, PartnerProductForm } from "../src/app/partner-product-form";
import { PartnerProductPage } from "../src/app/partner-product-page";
import { partnerAccount } from "./partner-fixtures";
import type { ProductDetailOut } from "../src/lib/api/generated/models";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account = partnerAccount();
const unavailable = vi.fn();
const product: ProductDetailOut = {
  id: "33333333-3333-4333-8333-333333333333",
  account_id: account.id,
  account_name: account.name,
  name: "CronWatch",
  product_url: "https://cronwatch.example/",
  description: "Monitor scheduled jobs.",
  pricing: "paid",
  technologies: [],
  status: "pending",
  assessment: { state: "waiting", reason: "" },
  revision: 4,
  source: "manual",
  manual_active: true,
  editable: true,
  active: true,
  excluded: false,
  globally_excluded: false,
  commercial_approved: false,
  ad_formats: null,
  promotion_surfaces: null,
  ad_image_url: null,
};
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function edit() {
  return render(
    <PartnerProductForm
      account={account}
      product={product}
      csrfToken="csrf"
      onUnavailable={unavailable}
      cancelHref="/products"
    />,
  );
}
function record(overrides: Partial<ProductDetailOut> = {}, action?: "edit" | "archive") {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...product, ...overrides })));
  return render(
    <PartnerProductPage
      account={account}
      productId={product.id}
      action={action}
      csrfToken="csrf"
      onUnavailable={unavailable}
    />,
  );
}
async function fill() {
  fireEvent.change(screen.getByRole("textbox", { name: "Product name" }), {
    target: { value: product.name },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Website URL" }), {
    target: { value: product.product_url },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: product.description },
  });
}
it("creates a direct product without a connector and opens the validated record", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(product, { status: 201 }));
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerProductCreate account={account} csrfToken="csrf" onUnavailable={unavailable} />);
  await fill();
  await userEvent.click(screen.getByRole("button", { name: "Add product" }));
  await waitFor(() =>
    expect(router.replace).toHaveBeenCalledWith(`/products/${product.id}?account_id=${account.id}`),
  );
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/products`,
    expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "x-csrf-token": "csrf" }),
      body: JSON.stringify({
        name: product.name,
        product_url: product.product_url,
        description: product.description,
        pricing: "unknown",
      }),
    }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("combobox", { name: /connection/i })).toBeNull();
});
it("can select an owning account beyond the first page without assuming the first membership", async () => {
  const other = partnerAccount({
    id: "22222222-2222-4222-8222-222222222222",
    name: "Other organization",
  });
  const fetcher = vi.fn().mockImplementation((url: string, init: RequestInit) =>
    Response.json(
      init.method === "POST"
        ? { ...product, account_id: other.id, account_name: other.name }
        : {
            items: [url.includes("offset=25") ? other : account],
            total: 26,
            offset: url.includes("offset=25") ? 25 : 0,
            limit: 25,
          },
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<PartnerProductCreate csrfToken="csrf" onUnavailable={unavailable} />);
  await userEvent.click(screen.getByRole("combobox", { name: "Partner account" }));
  await screen.findByRole("option", { name: account.name });
  await userEvent.click(screen.getByRole("button", { name: "Next accounts" }));
  await userEvent.click(await screen.findByRole("option", { name: other.name }));
  await fill();
  await userEvent.click(screen.getByRole("button", { name: "Add product" }));
  await waitFor(() =>
    expect(router.replace).toHaveBeenCalledWith(`/products/${product.id}?account_id=${other.id}`),
  );
  expect(fetcher.mock.calls.filter(([url]) => url.includes("connections"))).toHaveLength(0);
});
it("keeps a failed creation draft intact and rejects a returned product from another account", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { ...product, account_id: "22222222-2222-4222-8222-222222222222" },
          { status: 201 },
        ),
      ),
  );
  render(<PartnerProductCreate account={account} csrfToken="csrf" onUnavailable={unavailable} />);
  await fill();
  await userEvent.click(screen.getByRole("button", { name: "Add product" }));
  await screen.findByRole("alert");
  expect((screen.getByRole("textbox", { name: "Description" }) as HTMLTextAreaElement).value).toBe(
    product.description,
  );
  expect(router.replace).not.toHaveBeenCalled();
});
it("sends the expected facts revision and keeps stale edits on the form", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  edit();
  fireEvent.change(screen.getByRole("textbox", { name: "Product name" }), {
    target: { value: "CronWatch Pro" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save product" }));
  await screen.findByText(/This product changed/);
  expect(JSON.parse(String(fetcher.mock.calls[0][1].body))).toMatchObject({
    expected_revision: 4,
    name: "CronWatch Pro",
  });
  expect((screen.getByRole("textbox", { name: "Product name" }) as HTMLInputElement).value).toBe(
    "CronWatch Pro",
  );
  expect(router.replace).not.toHaveBeenCalled();
});
it("shows imported facts as read-only on an edit bookmark", async () => {
  record({ source: "imported", manual_active: null, editable: false }, "edit");
  await screen.findByRole("heading", { name: "Product details are managed elsewhere" });
  expect(screen.queryByRole("textbox", { name: "Product name" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save product" })).toBeNull();
});
it("archives an owned shared-catalog product on a confirmation page without requiring edit rights", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url: string, init: RequestInit) =>
      init.method === "DELETE"
        ? new Response(null, { status: 204 })
        : Response.json({ ...product, editable: false }),
    );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage
      account={account}
      productId={product.id}
      action="archive"
      csrfToken="csrf"
      onUnavailable={unavailable}
    />,
  );
  await screen.findByRole("heading", { name: "Archive this product?" });
  expect(screen.queryByRole("dialog")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Archive product" }));
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/products"));
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}`,
    expect.objectContaining({
      method: "DELETE",
      headers: expect.objectContaining({ "x-csrf-token": "csrf" }),
    }),
  );
});
it("restores an archived manual product from its bookmarked record", async () => {
  let active = false;
  const fetcher = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    if (init.method === "PATCH") active = true;
    return Response.json({ ...product, active, manual_active: active });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage
      account={account}
      productId={product.id}
      csrfToken="csrf"
      onUnavailable={unavailable}
    />,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Restore product" }));
  await screen.findByRole("link", { name: "Edit product" });
  expect(
    JSON.parse(String(fetcher.mock.calls.find(([, init]) => init.method === "PATCH")![1].body)),
  ).toEqual({ active: true, expected_revision: 4 });
});
it("cancels a pending product creation and ignores a late unauthorized reply", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(
    <PartnerProductCreate account={account} csrfToken="csrf" onUnavailable={unavailable} />,
  );
  await fill();
  await userEvent.click(screen.getByRole("button", { name: "Add product" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(new Response(null, { status: 401 })));
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

it("keeps imported placements available when only the manual entry is archived", async () => {
  record({ active: true, manual_active: false });
  await screen.findByRole("heading", { name: "Manually added entry archived" });
  expect(screen.getByRole("link", { name: "Edit placements" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Restore product" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Archive product" })).toBeNull();
  expect(screen.getByText(/active import connection keeps this product available/)).toBeTruthy();
});
it("does not confirm restoration from aggregate availability alone", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ ...product, active: true, manual_active: false }));
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage
      account={account}
      productId={product.id}
      csrfToken="csrf"
      onUnavailable={unavailable}
    />,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Restore product" }));
  await screen.findByText("Could not restore this product. Please retry.");
  expect(screen.getByRole("button", { name: "Restore product" })).toBeTruthy();
});

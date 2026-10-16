// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerProductCatalog, productPlacementStatus } from "../src/app/partner-product-catalog";
import { PartnerProductPage } from "../src/app/partner-product-page";
import { partnerAccount } from "./partner-fixtures";
import { AD_LINK_REL_OPTIONS } from "@devfeed/ui/ad-link-rel";

const { router } = vi.hoisted(() => ({ router: { replace: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
const account = partnerAccount();
const product = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "CronWatch",
  product_url: "https://cronwatch.example/",
  tagline: "Monitor scheduled jobs.",
  pricing: "paid" as const,
  assessment: { state: "qualified" as const, reason: "" },
  revision: 3,
  source: "imported" as const,
  manual_active: null,
  editable: false,
  active: true,
  status: "approved",
  technologies: ["Python", "PostgreSQL"],
  excluded: false,
  globally_excluded: false,
  commercial_approved: true,
  ad_formats: null,
  promotion_surfaces: null,
  ad_image_url: null,
  configurable_link_rel: false,
  effective_link_rel: ["nofollow" as const],
  link_rel_options: [...AD_LINK_REL_OPTIONS],
};
const policy = {
  account_id: account.id,
  product_id: product.id,
  excluded: false,
  ad_formats: null,
  promotion_surfaces: null,
  ad_image_url: null,
  image_url: null,
  default_image_url: null,
  ad_image_status: "default" as const,
};
const creative = {
  link_rel: ["nofollow"],
  id: product.id,
  name: product.name,
  tagline: "Monitor scheduled jobs.",
  logo_url: null,
  image_url: null,
  ad_image_status: "default" as const,
  product_url: "https://cronwatch.example/",
  listing_url: null,
};
const unavailable = vi.fn();
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function page(edit = false) {
  return render(
    <PartnerProductPage
      account={account}
      productId={product.id}
      edit={edit}
      csrfToken="csrf"
      onUnavailable={unavailable}
      query="?q=cron&product_offset=25"
    />,
  );
}
function load(url: string) {
  return Response.json(
    url.includes("creative-preview")
      ? { product: creative }
      : url.includes("/promotion")
        ? policy
        : { ...product, account_id: account.id, account_name: account.name },
  );
}

it("lists imported products as compact records with detail and edit links", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      Response.json({
        items: [{ ...product, account_id: account.id, account_name: account.name }],
        total: 1,
        limit: 25,
        offset: 0,
      }),
    ),
  );
  render(<PartnerProductCatalog onUnavailable={unavailable} />);
  const table = screen.getByRole("table", { name: "Products" });
  const link = await within(table).findByRole("link", { name: product.name });
  expect(link.getAttribute("href")).toBe(`/products/${product.id}?account_id=${account.id}`);
  expect(
    within(table)
      .getByRole("link", { name: `Edit placements for ${product.name}` })
      .getAttribute("href"),
  ).toBe(`/products/${product.id}/placements?account_id=${account.id}`);
  expect(screen.queryByRole("columnheader", { name: "Impressions" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Placements" })).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("filters products from the account column header and clears to all accounts without losing other filters", async () => {
  const other = partnerAccount({
    id: "22222222-2222-4222-8222-222222222222",
    name: "Off-page organization",
  });
  const fetcher = vi.fn().mockImplementation((input: string) => {
    const url = new URL(input, "http://localhost");
    if (url.pathname.endsWith("/accounts")) {
      const q = url.searchParams.get("q") ?? "";
      const matches = [account, other].filter((item) => item.name.includes(q));
      return Response.json({ items: matches, total: matches.length });
    }
    const selectedId = url.searchParams.get("account_id");
    const visible = [account, other].filter((item) => !selectedId || item.id === selectedId);
    return Response.json({
      items: visible.map((item) => ({
        ...product,
        account_id: item.id,
        account_name: item.name,
      })),
      total: 50,
      offset: Number(url.searchParams.get("offset")),
      limit: 25,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductCatalog
      onUnavailable={unavailable}
      accountFilter={account}
      initialSearch="Cron"
      initialOffset={25}
      initialIncludeArchived
      query="?days=7&sort=clicks"
    />,
  );
  const table = screen.getByRole("table", { name: "Products" });
  const header = within(table).getByRole("columnheader", { name: /Partner account/ });
  const filter = within(header).getByRole("combobox", {
    name: "Filter products by partner account",
  });
  await within(table).findByRole("link", { name: account.name });
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/accounts?"))).toBe(false);
  expect(filter.textContent).toContain(account.name);
  await userEvent.click(filter);
  await screen.findByRole("option", { name: other.name });
  await userEvent.type(screen.getByPlaceholderText("Search partner accounts…"), "Off-page");
  await waitFor(() =>
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/partner/accounts?limit=25&offset=0&q=Off-page",
      expect.anything(),
    ),
  );
  await userEvent.click(await screen.findByRole("option", { name: other.name }));
  await within(table).findByRole("link", { name: other.name });
  expect(within(table).queryByRole("link", { name: account.name })).toBeNull();
  let params = new URLSearchParams(window.location.search);
  expect(params.get("account_id")).toBe(other.id);
  expect(params.get("q")).toBe("Cron");
  expect(params.get("include_archived")).toBe("true");
  expect(params.get("days")).toBe("7");
  expect(params.get("sort")).toBe("clicks");
  expect(params.has("product_offset")).toBe(false);
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/inventory?q=Cron&offset=0&limit=25&account_id=${other.id}&include_archived=true`,
    expect.anything(),
  );
  expect(screen.queryByText(/Filtered to/)).toBeNull();
  await userEvent.click(filter);
  await userEvent.click(await screen.findByRole("option", { name: "All accounts" }));
  await within(table).findByRole("link", { name: account.name });
  await within(table).findByRole("link", { name: other.name });
  params = new URLSearchParams(window.location.search);
  expect(params.has("account_id")).toBe(false);
  expect(params.get("q")).toBe("Cron");
  expect(params.get("include_archived")).toBe("true");
  expect(filter.textContent).toContain("All accounts");
});

it("resolves a bookmarked product independently of list pagination and previews only that product", async () => {
  const fetcher = vi.fn().mockImplementation(load);
  vi.stubGlobal("fetch", fetcher);
  page();
  await screen.findByRole("heading", { name: product.name, level: 1 });
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}`,
    expect.anything(),
  );
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/creative-preview?product_id=${product.id}`,
    expect.anything(),
  );
  expect(screen.getByRole("link", { name: "Edit placements" }).getAttribute("href")).toBe(
    `/products/${product.id}/placements?q=cron&product_offset=25&account_id=${account.id}`,
  );
  expect(screen.getByRole("link", { name: "Products" }).getAttribute("href")).toBe(
    `/products?q=cron&product_offset=25`,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.title).toBe("CronWatch · DevFeed Partners");
});

it.each([{ items: [] }, { items: [{ ...product, id: "44444444-4444-4444-8444-444444444444" }] }])(
  "keeps missing or mismatched products local to the record page",
  async ({ items }) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Response.json({ items, total: items.length })),
    );
    page();
    await screen.findByRole("heading", { name: "Product unavailable" });
    expect(screen.queryByRole("link", { name: "Edit placements" })).toBeNull();
    expect(unavailable).not.toHaveBeenCalled();
    expect(router.replace).not.toHaveBeenCalled();
  },
);

it("edits placements on a page, confirms the saved policy and returns to the record", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((url: string, init: RequestInit) =>
      init.method === "PUT"
        ? Response.json({ ...policy, ...JSON.parse(String(init.body)) })
        : load(url),
    );
  vi.stubGlobal("fetch", fetcher);
  page(true);
  await screen.findByRole("heading", { name: "Edit CronWatch placements" });
  const availability = await screen.findByRole("combobox", { name: "Product availability" });
  await userEvent.click(availability);
  await userEvent.click(screen.getByRole("option", { name: "Exclude this product" }));
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(router.replace).toHaveBeenCalledWith(
      `/products/${product.id}?q=cron&product_offset=25&account_id=${account.id}`,
    ),
  );
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/accounts/${account.id}/products/${product.id}/promotion`,
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        excluded: true,
        ad_formats: null,
        promotion_surfaces: null,
        ad_image_url: null,
      }),
    }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("keeps a failed or unconfirmed save on the edit page with its draft intact", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(load));
  page(true);
  await screen.findByRole("combobox", { name: "Product availability" });
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Exclude this product" }));
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await screen.findByText(/Could not confirm saved product placements/);
  expect(screen.getByRole("combobox", { name: "Product availability" }).textContent).toContain(
    "Exclude this product",
  );
  expect(router.replace).not.toHaveBeenCalled();
});

it("cancels a pending product read on navigation and ignores a late unauthorized reply", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetcher);
  const view = page();
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(new Response(null, { status: 401 })));
  expect(unavailable).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

it("shows trusted accepted products as allowed while capability review continues", () => {
  expect(
    productPlacementStatus({ ...product, status: "pending", approval_source: "trusted" }),
  ).toBe("Allowed");
  expect(productPlacementStatus({ ...product, commercial_approved: false })).toBe("In review");
});

it("resolves a product's sole authorized account through one targeted workspace lookup", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      items: [{ ...product, account_id: account.id, account_name: account.name }],
      total: 1,
      limit: 25,
      offset: 0,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage productId={product.id} csrfToken="csrf" onUnavailable={unavailable} />,
  );
  await waitFor(() =>
    expect(router.replace).toHaveBeenCalledWith(`/products/${product.id}?account_id=${account.id}`),
  );
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/inventory?product_id=${product.id}&limit=25&offset=0`,
    expect.anything(),
  );
});

it("keeps multiple account policies visible instead of selecting the first account", async () => {
  const beta = { id: "22222222-2222-4222-8222-222222222222", name: "Beta" };
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      items: [
        { ...product, account_id: account.id, account_name: account.name },
        { ...product, account_id: beta.id, account_name: beta.name, excluded: true },
      ],
      total: 2,
      limit: 25,
      offset: 0,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage productId={product.id} csrfToken="csrf" onUnavailable={unavailable} />,
  );
  const table = await screen.findByRole("table", { name: "Product account policies" });
  expect(within(table).getAllByRole("row")).toHaveLength(3);
  expect(within(table).getByRole("link", { name: beta.name }).getAttribute("href")).toBe(
    `/products/${product.id}?account_id=${beta.id}`,
  );
  expect(within(table).getByText("Excluded")).toBeTruthy();
  expect(router.replace).not.toHaveBeenCalled();
  expect(screen.queryByRole("combobox", { name: "Partner account" })).toBeNull();
});

it("opens a dedicated policy chooser for an ambiguous placement-edit bookmark", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        items: [{ ...product, account_id: account.id, account_name: account.name }],
        total: 2,
        limit: 25,
        offset: 0,
      }),
    ),
  );
  render(
    <PartnerProductPage productId={product.id} edit csrfToken="csrf" onUnavailable={unavailable} />,
  );
  await screen.findByRole("heading", { name: "Choose a placement policy" });
  expect(screen.queryByRole("button", { name: "Save product placements" })).toBeNull();
  expect(screen.getByRole("link", { name: account.name }).getAttribute("href")).toBe(
    `/products/${product.id}/placements?account_id=${account.id}`,
  );
});

it("shows archived products on request while preserving search and account filters", async () => {
  const fetcher = vi.fn().mockImplementation((url: string) => {
    const params = new URL(url, "https://partner.example").searchParams;
    return Response.json({
      items: [
        {
          ...product,
          account_id: account.id,
          account_name: account.name,
          active: params.get("include_archived") !== "true",
          source: "manual",
          manual_active: params.get("include_archived") !== "true",
        },
      ],
      total: 60,
      limit: 25,
      offset: Number(params.get("offset")),
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductCatalog
      accountFilter={account}
      onUnavailable={unavailable}
      initialSearch="cron"
      initialOffset={25}
      query="?days=7"
    />,
  );
  await screen.findByRole("link", { name: product.name });
  await userEvent.click(screen.getByRole("checkbox", { name: "Show archived products" }));
  const table = screen.getByRole("table", { name: "Products" });
  await within(table).findByText("Archived");
  expect(fetcher).toHaveBeenLastCalledWith(
    `/api/v1/partner/inventory?q=cron&offset=0&limit=25&account_id=${account.id}&include_archived=true`,
    expect.anything(),
  );
  expect(new URLSearchParams(window.location.search).get("product_offset")).toBeNull();
  expect(new URLSearchParams(window.location.search).get("q")).toBe("cron");
  expect(new URLSearchParams(window.location.search).get("days")).toBe("7");
  expect(new URLSearchParams(window.location.search).get("include_archived")).toBe("true");
  expect(
    within(table)
      .getByRole("link", { name: `Restore ${product.name}` })
      .getAttribute("href"),
  ).toContain(`account_id=${account.id}`);
  expect(
    within(table)
      .getByRole("link", { name: `Restore ${product.name}` })
      .getAttribute("href"),
  ).not.toContain("/placements");
  await userEvent.click(screen.getByRole("button", { name: "Next products" }));
  await within(table).findByText("Archived");
  expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining("offset=25"), expect.anything());
  expect(fetcher).toHaveBeenLastCalledWith(
    expect.stringContaining("include_archived=true"),
    expect.anything(),
  );
  await userEvent.click(screen.getByRole("checkbox", { name: "Show archived products" }));
  await within(table).findByText("Allowed");
  expect(new URLSearchParams(window.location.search).has("include_archived")).toBe(false);
  expect(new URLSearchParams(window.location.search).has("product_offset")).toBe(false);
});
it("hydrates archived products from the initial filter and labels inactive blocked entries as archived", async () => {
  const archived = {
    ...product,
    active: false,
    manual_active: false,
    source: "manual" as const,
    globally_excluded: true,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        items: [{ ...archived, account_id: account.id, account_name: account.name }],
        total: 1,
        limit: 25,
        offset: 0,
      }),
    ),
  );
  render(<PartnerProductCatalog onUnavailable={unavailable} initialIncludeArchived />);
  await screen.findByText("Archived");
  expect(
    screen.getByRole("checkbox", { name: "Show archived products" }).getAttribute("aria-checked"),
  ).toBe("true");
  expect(productPlacementStatus(archived)).toBe("Archived");
});

it("keeps the archived filter when resolving a product's account policy", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    Response.json({
      items: [
        {
          ...product,
          active: false,
          manual_active: false,
          source: "manual",
          account_id: account.id,
          account_name: account.name,
        },
      ],
      total: 1,
      limit: 25,
      offset: 0,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  render(
    <PartnerProductPage
      productId={product.id}
      csrfToken="csrf"
      onUnavailable={unavailable}
      query="?q=cron&include_archived=true"
    />,
  );
  await waitFor(() =>
    expect(router.replace).toHaveBeenCalledWith(
      `/products/${product.id}?q=cron&include_archived=true&account_id=${account.id}`,
    ),
  );
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/partner/inventory?product_id=${product.id}&limit=25&offset=0&include_archived=true`,
    expect.anything(),
  );
});

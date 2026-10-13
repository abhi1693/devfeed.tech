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
  ad_image_status: "default" as const,
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
  ad_image_url: null,
  image_url: "https://assets.example/website.webp",
  default_image_url: "https://assets.example/website.webp",
  ad_image_status: "default" as const,
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
async function chooseCustomImage() {
  expect(screen.queryByRole("button", { name: "Upload image" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Ad image URL" })).toBeNull();
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
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
      body: JSON.stringify({
        excluded: true,
        ad_formats: null,
        promotion_surfaces: null,
        ad_image_url: null,
      }),
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
      body: JSON.stringify({
        excluded: false,
        ad_formats: null,
        promotion_surfaces: null,
        ad_image_url: null,
      }),
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
          product_url: "https://cronwatch.example/",
          description: "Monitor scheduled jobs.",
          pricing: "paid",
          assessment: { state: "qualified", reason: "" },
          revision: 1,
          source: "imported",
          manual_active: null,
          editable: false,
          active: true,
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
  await userEvent.click(screen.getByRole("button", { name: "Next products" }));
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
    (screen.getByRole("button", { name: "Previous products" }) as HTMLButtonElement).disabled,
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
  await userEvent.click(screen.getByRole("button", { name: "Retry products" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const signal = fetcher.mock.calls[1][1].signal as AbortSignal;
  expect(signal.aborted).toBe(false);
  view.unmount();
  expect(signal.aborted).toBe(true);
});

it("previews an unsaved image URL locally and saves only editable product settings", async () => {
  const image = "https://assets.example/partner-campaign.webp";
  const fetcher = vi.fn().mockImplementation((url: string, init: RequestInit) => {
    if (url.includes("creative-preview"))
      return Response.json({
        product: { ...product, image_url: policy.default_image_url, ad_image_status: "default" },
      });
    if (init.method === "PUT")
      return Response.json({
        ...policy,
        ...JSON.parse(String(init.body)),
        ad_image_status: "queued",
      });
    return Response.json(policy);
  });
  vi.stubGlobal("fetch", fetcher);
  placements();
  await openProductPlacements();
  const preview = await screen.findByRole("region", { name: "Creative preview" });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(policy.default_image_url));
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Ad image URL" }), {
    target: { value: image },
  });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(image));
  expect(fetcher.mock.calls.filter(([, init]) => init.method === "PUT")).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  const write = fetcher.mock.calls.find(([, init]) => init.method === "PUT");
  expect(JSON.parse(String(write?.[1].body))).toEqual({
    excluded: true,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: image,
  });
});

it.each(["queued", "running", "failed"] as const)(
  "uses the website fallback while the saved custom image is %s",
  async (status) => {
    const fetcher = vi.fn().mockImplementation((url: string) =>
      Response.json(
        url.includes("creative-preview")
          ? {
              product: {
                ...product,
                image_url: policy.default_image_url,
                ad_image_status: status,
              },
            }
          : {
              ...policy,
              ad_image_url: "https://assets.example/not-ready.webp",
              ad_image_status: status,
            },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    const view = placements();
    await openProductPlacements();
    const preview = await screen.findByRole("region", { name: "Creative preview" });
    await waitFor(() => expect(preview.querySelector("img")?.src).toBe(policy.default_image_url));
    expect((screen.getByRole("textbox", { name: "Ad image URL" }) as HTMLInputElement).value).toBe(
      "https://assets.example/not-ready.webp",
    );
    expect(screen.getByText("Saved product settings")).toBeTruthy();
    expect(fetcher.mock.calls.filter(([, init]) => init.method === "PUT")).toHaveLength(0);
    view.unmount();
  },
);

it("keeps the partner image draft when the save response does not confirm its URL", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      Response.json(url.includes("creative-preview") ? { product } : policy),
    );
  vi.stubGlobal("fetch", fetcher);
  placements();
  await openProductPlacements();
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  const input = screen.getByRole("textbox", { name: "Ad image URL" }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://assets.example/new.webp" } });
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await screen.findByText(/Could not confirm saved product placements/);
  expect(input.value).toBe("https://assets.example/new.webp");
  expect(saved).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
});

it("uploads multipart images with partner CSRF and keeps the edit page open", async () => {
  const uploadedUrl = "https://images.example/partner-ad-images/800.webp?v=revision-1";
  const fetcher = vi.fn().mockImplementation((url: string, init: RequestInit) => {
    if (init.method === "POST")
      return Response.json({
        ...policy,
        ad_image_url: uploadedUrl,
        image_url: uploadedUrl,
        ad_image_status: "ready",
      });
    return Response.json(url.includes("creative-preview") ? { product } : policy);
  });
  vi.stubGlobal("fetch", fetcher);
  placements();
  await openProductPlacements();
  const file = new File(["jpeg-bytes"], "ad.jpg", { type: "image/jpeg" });
  await chooseCustomImage();
  fireEvent.change(screen.getByLabelText("Upload ad image file"), { target: { files: [file] } });
  await screen.findByText(/Image uploaded and applied/);
  const write = fetcher.mock.calls.find(([, init]) => init.method === "POST")!;
  expect(write[0]).toBe(`/api/v1/partner/accounts/${account.id}/products/${product.id}/ad-image`);
  expect(write[1].headers).toEqual({ "x-csrf-token": "partner-csrf" });
  expect((write[1].body as FormData).get("file")).toBe(file);
  expect(saved).not.toHaveBeenCalled();
  expect(router.replace).not.toHaveBeenCalled();
  expect((screen.getByRole("textbox", { name: "Ad image URL" }) as HTMLInputElement).value).toBe(
    uploadedUrl,
  );
  const preview = await screen.findByRole("region", { name: "Creative preview" });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(uploadedUrl));
});

it.each(["account_id", "product_id"])(
  "rejects an upload response for another %s",
  async (field) => {
    const uploadedUrl = "https://images.example/800.webp?v=revision-1";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init: RequestInit) =>
        Response.json(
          init.method === "POST"
            ? {
                ...policy,
                [field]: "another-record",
                ad_image_url: uploadedUrl,
                image_url: uploadedUrl,
                ad_image_status: "ready",
              }
            : url.includes("creative-preview")
              ? { product }
              : policy,
        ),
      ),
    );
    placements();
    await openProductPlacements();
    await chooseCustomImage();
    fireEvent.change(screen.getByLabelText("Upload ad image file"), {
      target: { files: [new File(["webp"], "ad.webp", { type: "image/webp" })] },
    });
    await screen.findByText("Could not confirm the uploaded ad image. Refresh and try again.");
    expect(screen.queryByText(/Image uploaded and applied/)).toBeNull();
    expect(saved).not.toHaveBeenCalled();
  },
);

it.each([
  [413, "Choose an image under 5 MB."],
  [422, "Choose a valid JPEG, PNG or WebP image."],
])("shows useful upload validation errors (%s)", async (status, message) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation((url: string, init: RequestInit) =>
        init.method === "POST"
          ? new Response(null, { status })
          : Response.json(url.includes("creative-preview") ? { product } : policy),
      ),
  );
  placements();
  await openProductPlacements();
  await chooseCustomImage();
  fireEvent.change(screen.getByLabelText("Upload ad image file"), {
    target: { files: [new File(["png"], "ad.png", { type: "image/png" })] },
  });
  await screen.findByText(message);
  expect((screen.getByRole("button", { name: "Upload image" }) as HTMLButtonElement).disabled).toBe(
    false,
  );
  expect(saved).not.toHaveBeenCalled();
});

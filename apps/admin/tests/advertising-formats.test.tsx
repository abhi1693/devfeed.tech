// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  AdCreativePreview,
  ProductPlacementDialog,
  ProductPlacementPreview,
  adFormats,
  parseCreativePreview,
  parseProductPlacementPolicy,
  type ProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";

const product = {
  id: "product-1",
  name: "Rust deploy",
  tagline: "Deploy Rust services with trusted operations.",
  product_url: "https://rust.example/?utm_source=devfeed",
  listing_url: "https://partner.example/rust?utm_source=devfeed",
  image_url: "https://assets.example/rust.webp",
  logo_url: "https://assets.example/rust-logo.webp",
};
const allowed = adFormats.map((format) => format.value);
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function chooseCustomImage() {
  expect(screen.queryByRole("button", { name: "Upload image" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Ad image URL" })).toBeNull();
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
}

it.each(["feed", "homepage_sponsorship", "engagement"] as const)(
  "previews actual %s creative with a Sponsored pill and no visit CTA",
  (format) => {
    render(<AdCreativePreview format={format} product={product} />);
    const preview = screen.getByRole("region", { name: "Creative preview" });
    expect(within(preview).getByText(product.name)).toBeTruthy();
    expect(within(preview).getByText("Sponsored").className).toContain(
      format === "homepage_sponsorship" ? "text-muted-foreground" : "rounded-full",
    );
    expect(
      format === "homepage_sponsorship"
        ? within(preview).getByTitle("rust.example")
        : within(preview).getByText("rust.example", { exact: true }),
    ).toBeTruthy();
    expect(preview.textContent).not.toMatch(/Visit website|Read article/);
    expect(preview.textContent).not.toContain("Explore");
    expect(preview.querySelectorAll("img").length).toBe(1);
  },
);

it("shows homepage sponsorship as a quiet credit in the desktop app header", () => {
  render(<AdCreativePreview format="homepage_sponsorship" product={product} />);
  const preview = screen.getByRole("region", { name: "Creative preview" });
  expect(
    within(preview).getByText(/top navigation of unfiltered Home and Latest feeds/),
  ).toBeTruthy();
  expect(within(preview).getByRole("article", { name: "Desktop navigation preview" })).toBeTruthy();
  for (const label of ["DevFeed", "Search", "Sign in"])
    expect(within(preview).getByText(label, { exact: true })).toBeTruthy();
  expect(within(preview).getByText(product.name)).toBeTruthy();
  expect(within(preview).getByText("Sponsored")).toBeTruthy();
  expect(within(preview).getByTitle("rust.example").textContent).toContain(product.name);
  expect(preview.querySelector("img")?.src).toBe(product.logo_url);
  expect(within(preview).getByText(product.tagline)).toBeTruthy();
  expect(within(preview).getByText("Sponsored").className).not.toMatch(/amber|rounded-full/);
  expect(preview.querySelector("img")?.className).toContain("size-8");
  expect(within(preview).queryByText("rust.example", { exact: true })).toBeNull();
  expect(within(preview).queryByText(/Explore|Visit website/)).toBeNull();
  expect(within(preview).queryByRole("link")).toBeNull();
  expect(within(preview).queryByRole("button")).toBeNull();
});

it("explains contextual engagement placement and retains its product tagline and hostname", () => {
  render(<AdCreativePreview format="engagement" product={product} />);
  const preview = screen.getByRole("region", { name: "Creative preview" });
  expect(within(preview).getByText(/search and article sidebars/)).toBeTruthy();
  expect(within(preview).getByText(product.tagline)).toBeTruthy();
  expect(within(preview).getByText("rust.example", { exact: true })).toBeTruthy();
  expect(within(preview).queryByText(/Explore/)).toBeNull();
  expect(within(preview).queryByText("DevFeed")).toBeNull();
});

it("previews a feed without a hero using compact product branding", () => {
  const view = render(
    <AdCreativePreview format="feed" product={{ ...product, image_url: null }} />,
  );
  expect(view.container.querySelector("img")?.src).toBe(product.logo_url);
  expect(view.container.querySelector(".aspect-video")).toBeNull();
  expect(screen.getByText(product.name)).toBeTruthy();
  expect(screen.getByText("Sponsored")).toBeTruthy();
});

it.each([null, product.image_url])(
  "previews the importing platform's managed source logo with hero %s",
  (image_url) => {
    const platform = {
      name: "Launch platform",
      logo_url: "https://images.example/platform/64.webp?v=one",
      logo_variants: [16, 32, 64, 96].map((width) => ({
        width,
        url: `https://images.example/platform/${width}.webp?v=one`,
      })),
    };
    const view = render(
      <AdCreativePreview format="feed" product={{ ...product, image_url, platform }} />,
    );
    const logo = view.container.querySelector(`img[src="${platform.logo_url}"]`)!;
    expect(screen.getByText(platform.name)).toBeTruthy();
    expect(logo.getAttribute("sizes")).toBe("16px");
    expect(logo.getAttribute("srcset")).toContain("/16.webp?v=one 16w");
    expect(logo.getAttribute("referrerpolicy")).toBe("no-referrer");
    fireEvent.error(logo);
    expect(view.container.querySelector(`img[src="${platform.logo_url}"]`)).toBeNull();
    expect(screen.getByText(platform.name)).toBeTruthy();
    expect(screen.getByText("Sponsored")).toBeTruthy();
  },
);

it.each([
  {},
  { name: "Platform", logo_url: null, logo_variants: "invalid" },
  {
    name: "Platform",
    logo_url: null,
    logo_variants: [{ url: "https://images.example/logo.webp", width: 0 }],
  },
])("rejects malformed preview platform metadata (%j)", (platform) => {
  expect(() => parseCreativePreview({ product: { ...product, platform } })).toThrow();
});

it("previews the approved article's internal destination and title", () => {
  render(
    <AdCreativePreview
      format="sponsored_content"
      article={{
        id: "article-1",
        title: "Reliable Rust deployments",
        slug: "reliable-rust",
        summary: "Learn reliable delivery.",
        image_url: "https://assets.example/article.webp",
        canonical_url: "https://publisher.example/original",
      }}
    />,
  );
  expect(screen.getByText("Reliable Rust deployments")).toBeTruthy();
  expect(screen.getByText("/articles/reliable-rust")).toBeTruthy();
  expect(screen.queryByText(/publisher.example/)).toBeNull();
});

it("falls back from managed hero to logo then initials", () => {
  const view = render(<AdCreativePreview format="feed" product={product} />);
  fireEvent.error(view.container.querySelector("img")!);
  expect(view.container.querySelector("img")?.src).toBe(product.logo_url);
  fireEvent.error(view.container.querySelector("img")!);
  expect(screen.getByText("RD")).toBeTruthy();
});

it("validates preview and product-policy boundaries, preserving null versus empty restrictions", () => {
  expect(parseCreativePreview({ product })).toEqual(product);
  expect(() => parseCreativePreview({ product: { ...product, tagline: null } })).toThrow();
  expect(
    parseProductPlacementPolicy({
      excluded: false,
      ad_formats: null,
      promotion_surfaces: [],
      ad_image_url: null,
    }),
  ).toMatchObject({ ad_formats: null, promotion_surfaces: [], ad_image_url: null });
  expect(() =>
    parseProductPlacementPolicy({
      excluded: null,
      ad_formats: [],
      promotion_surfaces: null,
      ad_image_url: null,
    }),
  ).toThrow();
  expect(() =>
    parseProductPlacementPolicy({
      excluded: false,
      ad_formats: ["newsletter"],
      promotion_surfaces: null,
      ad_image_url: null,
    }),
  ).toThrow();
});

it("requires a concise product tagline and does not substitute a legacy description", () => {
  const { tagline, ...withoutTagline } = product;
  expect(() =>
    parseCreativePreview({ product: { ...withoutTagline, description: tagline } }),
  ).toThrow();
  expect(() => parseCreativePreview({ product: { ...product, tagline: "AI" } })).toThrow();
  expect(() =>
    parseCreativePreview({ product: { ...product, tagline: "x".repeat(161) } }),
  ).toThrow();
  expect(parseCreativePreview({ product: { ...product, tagline: "AI IDE" } })?.tagline).toBe(
    "AI IDE",
  );
});

it("requires an explicit image choice and validates optional processing metadata", () => {
  const policy = {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  expect(() => parseProductPlacementPolicy({ ...policy, ad_image_url: undefined })).toThrow();
  expect(() => parseProductPlacementPolicy({ ...policy, ad_image_url: 42 })).toThrow();
  expect(() =>
    parseProductPlacementPolicy({ ...policy, ad_image_url: "javascript:alert(1)" }),
  ).toThrow();
  expect(parseProductPlacementPolicy({ ...policy, ad_image_url: product.image_url })).toMatchObject(
    { ad_image_url: product.image_url },
  );
  expect(
    parseCreativePreview({ product: { ...product, ad_image_status: "queued" } }),
  ).toMatchObject({ ad_image_status: "queued" });
  expect(() =>
    parseCreativePreview({ product: { ...product, ad_image_status: "unknown" } }),
  ).toThrow();
});

it("lazily loads product restrictions and saves custom empty formats independently of exclusion", async () => {
  const inherited = {
    excluded: true,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  const load = vi.fn().mockResolvedValue(inherited);
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  const onSaved = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={load}
      savePolicy={save}
      onSaved={onSaved}
    />,
  );
  expect(load).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  const dialog = await screen.findByRole("dialog", { name: `${product.name} placements` });
  await within(dialog).findByText(/This product is excluded/);
  await userEvent.click(within(dialog).getByRole("combobox", { name: "Product formats" }));
  await userEvent.click(screen.getByRole("option", { name: "Choose product formats" }));
  for (const format of adFormats)
    await userEvent.click(within(dialog).getByRole("checkbox", { name: format.label }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      { excluded: true, ad_formats: [], promotion_surfaces: null, ad_image_url: null },
      expect.any(AbortSignal),
    ),
  );
  expect(onSaved).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("explicitly resets saved custom policy to account inheritance", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: ["engagement"],
    promotion_surfaces: ["article"],
    ad_image_url: null,
  };
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={save}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("checkbox", { name: "Engagement" });
  for (const [label, option] of [
    ["Product formats", "Inherit account formats"],
    ["Product placements", "Inherit account placements"],
  ]) {
    await userEvent.click(screen.getByRole("combobox", { name: label }));
    await userEvent.click(screen.getByRole("option", { name: option }));
  }
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      { excluded: false, ad_formats: null, promotion_surfaces: null, ad_image_url: null },
      expect.any(AbortSignal),
    ),
  );
});

it("saves an explicit Search-only product placement restriction", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: ["engagement"],
    promotion_surfaces: [],
    ad_image_url: null,
  };
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      accountFormats={["engagement"]}
      accountSurfaces={["search"]}
      loadPolicy={async () => policy}
      savePolicy={save}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await userEvent.click(await screen.findByRole("checkbox", { name: "Search results" }));
  expect(screen.getByText("Allowed locations after saving: Search results.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      { ...policy, promotion_surfaces: ["search"] },
      expect.any(AbortSignal),
    ),
  );
});

it.each([true, false])(
  "saves a deliberate change from excluded=%s and retains restrictions",
  async (excluded) => {
    const policy: ProductPlacementPolicy = {
      excluded,
      ad_formats: ["engagement"],
      promotion_surfaces: ["article"],
      ad_image_url: null,
    };
    const save = vi
      .fn(async (next: ProductPlacementPolicy) => next)
      .mockRejectedValueOnce(new Error("Save failed."));
    render(
      <ProductPlacementDialog
        name={product.name}
        allowedFormats={allowed}
        loadPolicy={async () => policy}
        savePolicy={save}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
    );
    await screen.findByText("Saved product settings");
    await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
    await userEvent.click(
      screen.getByRole("option", {
        name: excluded ? "Allow this product" : "Exclude this product",
      }),
    );
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    if (!excluded)
      expect(screen.getByText(/This product will be excluded after saving/)).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
    await screen.findByText("Save failed.");
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Product availability" }).textContent).toContain(
      excluded ? "Allow this product" : "Exclude this product",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    for (const [value] of save.mock.calls)
      expect(value).toEqual({ ...policy, excluded: !excluded });
  },
);

it("keeps a changed exclusion open when the server returns different saved settings", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: true,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  const onSaved = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={async () => policy}
      onSaved={onSaved}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Product availability" });
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Allow this product" }));
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await screen.findByText(
    "Could not confirm saved product placements. Review your changes and retry.",
  );
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(onSaved).not.toHaveBeenCalled();
});

it("discards unsaved availability changes on close and explains the inherited account limits", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  const save = vi.fn();
  const load = vi.fn().mockResolvedValue(policy);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      accountFormats={["engagement"]}
      accountSurfaces={["article"]}
      loadPolicy={load}
      savePolicy={save}
    />,
  );
  const trigger = screen.getByRole("button", { name: `Edit placements for ${product.name}` });
  await userEvent.click(trigger);
  await screen.findByText("Allowed automatic formats after saving: Engagement.");
  expect(screen.getByText("Allowed locations after saving: Article pages.")).toBeTruthy();
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Exclude this product" }));
  expect(screen.getByText("Allowed automatic formats after saving: None.")).toBeTruthy();
  expect(screen.getByText("Allowed locations after saving: None.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Close placements" }));
  await userEvent.click(trigger);
  await screen.findByText("Saved product settings");
  expect(screen.getByRole("combobox", { name: "Product availability" }).textContent).toContain(
    "Allow this product",
  );
  expect(load).toHaveBeenCalledTimes(2);
  expect(save).not.toHaveBeenCalled();
});

it("retains product edits on a failed save and supports retry without widening them", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: [],
    promotion_surfaces: [],
    ad_image_url: null,
  };
  const save = vi
    .fn()
    .mockRejectedValueOnce(new Error("Could not save product placements."))
    .mockResolvedValue(policy);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={save}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("checkbox", { name: "Engagement" });
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await screen.findByText("Could not save product placements.");
  expect(
    screen.getAllByRole("checkbox").every((checkbox) => !(checkbox as HTMLInputElement).checked),
  ).toBe(true);
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
});

it("preserves an unsaved product draft when an admin parent refreshes callbacks", async () => {
  const load = vi.fn().mockResolvedValue({
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  });
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  const view = render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={(signal) => load(signal)}
      savePolicy={save}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Product availability" });
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Exclude this product" }));
  view.rerender(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={[...allowed]}
      loadPolicy={(signal) => load(signal)}
      savePolicy={save}
    />,
  );
  expect(screen.getByRole("combobox", { name: "Product availability" }).textContent).toContain(
    "Exclude this product",
  );
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect(load).toHaveBeenCalledOnce();
});

it("previews a custom image draft and restores the website image without saving metadata", async () => {
  const websiteImage = "https://assets.example/website.webp";
  const customImage = "https://assets.example/campaign.webp";
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
    image_url: websiteImage,
    default_image_url: websiteImage,
    ad_image_status: "default",
  };
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={save}
      loadPreview={async () => ({ ...product, image_url: websiteImage })}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Ad image" });
  await userEvent.click(screen.getByRole("button", { name: "Preview this product" }));
  const preview = await screen.findByRole("region", { name: "Creative preview" });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(websiteImage));
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Ad image URL" }), {
    target: { value: customImage },
  });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(customImage));
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect(save).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Website image" }));
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(websiteImage));
  expect(screen.getByText("Saved product settings")).toBeTruthy();
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Ad image URL" }), {
    target: { value: customImage },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      { excluded: false, ad_formats: null, promotion_surfaces: null, ad_image_url: customImage },
      expect.any(AbortSignal),
    ),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("clears a saved override explicitly and confirms the server saved the clear", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: ["engagement"],
    promotion_surfaces: ["article"],
    ad_image_url: "https://assets.example/campaign.webp",
    image_url: "https://managed.example/campaign.webp",
    default_image_url: product.image_url,
    ad_image_status: "ready",
  };
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={save}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  const input = await screen.findByRole("textbox", { name: "Ad image URL" });
  expect((input as HTMLInputElement).value).toBe(policy.ad_image_url);
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Website image" }));
  expect(screen.queryByRole("textbox", { name: "Ad image URL" })).toBeNull();
  expect(save).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      {
        excluded: false,
        ad_formats: ["engagement"],
        promotion_surfaces: ["article"],
        ad_image_url: null,
      },
      expect.any(AbortSignal),
    ),
  );
});

it("rejects an image save that returns another URL and preserves the draft for retry", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  const onSaved = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={async () => policy}
      onSaved={onSaved}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Ad image" });
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  const input = screen.getByRole("textbox", { name: "Ad image URL" }) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "https://assets.example/new-campaign.webp" } });
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await screen.findByText(
    "Could not confirm saved product placements. Review your changes and retry.",
  );
  expect(input.value).toBe("https://assets.example/new-campaign.webp");
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  expect(onSaved).not.toHaveBeenCalled();
});

it("discards a custom image draft when the placement editor closes", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: null,
    promotion_surfaces: null,
    ad_image_url: null,
  };
  const save = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      savePolicy={save}
    />,
  );
  const trigger = screen.getByRole("button", { name: `Edit placements for ${product.name}` });
  await userEvent.click(trigger);
  await screen.findByRole("combobox", { name: "Ad image" });
  await userEvent.click(screen.getByRole("combobox", { name: "Ad image" }));
  await userEvent.click(screen.getByRole("option", { name: "Custom image" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Ad image URL" }), {
    target: { value: "https://assets.example/discarded.webp" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Close placements" }));
  await userEvent.click(trigger);
  await screen.findByText("Saved product settings");
  expect(screen.getByRole("combobox", { name: "Ad image" }).textContent).toContain("Website image");
  expect(screen.queryByRole("textbox", { name: "Ad image URL" })).toBeNull();
  expect(save).not.toHaveBeenCalled();
});

it("refreshes a pending creative until ready without clearing the visible fallback", async () => {
  vi.useFakeTimers();
  const managedImage = "https://managed.example/campaign.webp";
  const load = vi
    .fn()
    .mockResolvedValueOnce({ ...product, ad_image_status: "queued" })
    .mockResolvedValue({ ...product, image_url: managedImage, ad_image_status: "ready" });
  const view = render(
    <ProductPlacementPreview allowedFormats={allowed} initialFormat="feed" load={load} />,
  );
  await act(async () => {});
  expect(view.container.querySelector("img")?.src).toBe(product.image_url);
  expect(screen.getByRole("status", { name: "Ad image status" }).textContent).toContain(
    "Preparing the custom image",
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(view.container.querySelector("img")?.src).toBe(managedImage);
  expect(screen.getByRole("status", { name: "Ad image status" }).textContent).toContain(
    "Using the custom ad image",
  );
  expect(load).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(2);
});

it("bounds background image refreshes and cancels the pending refresh on navigation", async () => {
  vi.useFakeTimers();
  const load = vi.fn().mockResolvedValue({ ...product, ad_image_status: "running" });
  const view = render(
    <ProductPlacementPreview allowedFormats={allowed} initialFormat="feed" load={load} />,
  );
  await act(async () => {});
  for (let index = 0; index < 21; index++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
  expect(load).toHaveBeenCalledTimes(21);
  expect(view.container.querySelector("img")?.src).toBe(product.image_url);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(21);
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  await act(async () => {});
  expect(load).toHaveBeenCalledTimes(22);
  view.unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(load).toHaveBeenCalledTimes(22);
});

it("applies an upload immediately while preserving other unsaved product changes", async () => {
  const uploadedUrl = "https://images.example/ad-image/800.webp?v=upload-1";
  let policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: ["feed"],
    promotion_surfaces: ["article"],
    ad_image_url: null,
    image_url: product.image_url,
    default_image_url: product.image_url,
    ad_image_status: "default",
  };
  const uploadImage = vi.fn(async () => {
    policy = {
      ...policy,
      ad_image_url: uploadedUrl,
      image_url: uploadedUrl,
      ad_image_status: "ready",
    };
    return policy;
  });
  const save = vi.fn(async (next: ProductPlacementPolicy) => next);
  const onSaved = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => policy}
      loadPreview={async () => ({ ...product, image_url: policy.image_url! })}
      savePolicy={save}
      uploadImage={uploadImage}
      onSaved={onSaved}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Product availability" });
  await userEvent.click(screen.getByRole("combobox", { name: "Product availability" }));
  await userEvent.click(screen.getByRole("option", { name: "Exclude this product" }));
  const file = new File(["png-image"], "ad.png", { type: "image/png" });
  await chooseCustomImage();
  fireEvent.change(screen.getByLabelText("Upload ad image file"), { target: { files: [file] } });
  await screen.findByText(/Image uploaded and applied/);
  expect(uploadImage).toHaveBeenCalledWith(file, expect.any(AbortSignal));
  expect(save).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByRole("combobox", { name: "Product availability" }).textContent).toContain(
    "Exclude this product",
  );
  expect(screen.getByText("Unsaved changes")).toBeTruthy();
  const preview = await screen.findByRole("region", { name: "Creative preview" });
  await waitFor(() => expect(preview.querySelector("img")?.src).toBe(uploadedUrl));
  await userEvent.click(screen.getByRole("button", { name: "Save product placements" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      {
        excluded: true,
        ad_formats: ["feed"],
        promotion_surfaces: ["article"],
        ad_image_url: uploadedUrl,
      },
      expect.any(AbortSignal),
    ),
  );
  expect(onSaved).toHaveBeenCalledOnce();
});

it.each([
  [new File([], "empty.png", { type: "image/png" }), "Choose an image under 5 MB."],
  [
    new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }),
    "Choose an image under 5 MB.",
  ],
  [
    new File(["<svg/>"], "vector.svg", { type: "image/svg+xml" }),
    "Choose a JPEG, PNG or WebP image.",
  ],
])("rejects invalid image uploads before sending them (%s)", async (file, message) => {
  const uploadImage = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => ({
        excluded: false,
        ad_formats: null,
        promotion_surfaces: null,
        ad_image_url: null,
      })}
      savePolicy={vi.fn()}
      uploadImage={uploadImage}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Ad image" });
  await chooseCustomImage();
  fireEvent.change(screen.getByLabelText("Upload ad image file"), { target: { files: [file] } });
  await screen.findByText(message);
  expect(uploadImage).not.toHaveBeenCalled();
});

it("aborts a pending upload when the editor closes and ignores its late result", async () => {
  let finish!: (value: ProductPlacementPolicy) => void;
  const uploadImage = vi.fn<(file: File, signal: AbortSignal) => Promise<ProductPlacementPolicy>>(
    () =>
      new Promise<ProductPlacementPolicy>((resolve) => {
        finish = resolve;
      }),
  );
  const onSaved = vi.fn();
  render(
    <ProductPlacementDialog
      name={product.name}
      allowedFormats={allowed}
      loadPolicy={async () => ({
        excluded: false,
        ad_formats: null,
        promotion_surfaces: null,
        ad_image_url: null,
      })}
      savePolicy={vi.fn()}
      uploadImage={uploadImage}
      onSaved={onSaved}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: `Edit placements for ${product.name}` }),
  );
  await screen.findByRole("combobox", { name: "Ad image" });
  await chooseCustomImage();
  fireEvent.change(screen.getByLabelText("Upload ad image file"), {
    target: { files: [new File(["jpeg"], "ad.jpg", { type: "image/jpeg" })] },
  });
  await screen.findByRole("button", { name: "Uploading…" });
  expect(
    (screen.getByRole("button", { name: "Save product placements" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  const signal = uploadImage.mock.calls[0][1];
  await userEvent.click(screen.getByRole("button", { name: "Close placements" }));
  expect(signal.aborted).toBe(true);
  await act(async () =>
    finish({
      excluded: false,
      ad_formats: null,
      promotion_surfaces: null,
      ad_image_url: product.image_url,
      image_url: product.image_url,
      ad_image_status: "ready",
    }),
  );
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByText(/Image uploaded and applied/)).toBeNull();
});

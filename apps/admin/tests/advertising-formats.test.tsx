// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  AdCreativePreview,
  ProductPlacementDialog,
  adFormats,
  parseCreativePreview,
  parseProductPlacementPolicy,
  type ProductPlacementPolicy,
} from "@devfeed/ui/advertising-formats";

const product = {
  id: "product-1",
  name: "Rust deploy",
  description: "Deploy Rust services with trusted operations.",
  product_url: "https://rust.example/?utm_source=devfeed",
  listing_url: "https://partner.example/rust?utm_source=devfeed",
  image_url: "https://assets.example/rust.webp",
  logo_url: "https://assets.example/rust-logo.webp",
};
const allowed = adFormats.map((format) => format.value);
afterEach(cleanup);

it.each(["feed", "homepage_sponsorship", "engagement"] as const)(
  "previews actual %s creative with a Sponsored pill and no visit CTA",
  (format) => {
    render(<AdCreativePreview format={format} product={product} />);
    const preview = screen.getByRole("region", { name: "Creative preview" });
    expect(within(preview).getByText(product.name)).toBeTruthy();
    expect(within(preview).getByText("Sponsored").className).toContain("rounded-full");
    expect(preview.textContent).toContain("rust.example");
    expect(preview.textContent).not.toMatch(/Visit website|Read article|↗/);
    expect(preview.querySelectorAll("img").length).toBe(format === "engagement" ? 0 : 1);
  },
);

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
  expect(() => parseCreativePreview({ product: { ...product, description: null } })).toThrow();
  expect(
    parseProductPlacementPolicy({ excluded: false, ad_formats: null, promotion_surfaces: [] }),
  ).toMatchObject({ ad_formats: null, promotion_surfaces: [] });
  expect(() =>
    parseProductPlacementPolicy({ excluded: null, ad_formats: [], promotion_surfaces: null }),
  ).toThrow();
  expect(() =>
    parseProductPlacementPolicy({
      excluded: false,
      ad_formats: ["newsletter"],
      promotion_surfaces: null,
    }),
  ).toThrow();
});

it("lazily loads product restrictions and saves custom empty formats independently of exclusion", async () => {
  const inherited = { excluded: true, ad_formats: null, promotion_surfaces: null };
  const load = vi.fn().mockResolvedValue(inherited);
  const save = vi.fn().mockResolvedValue(inherited);
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
      { ad_formats: [], promotion_surfaces: null },
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
  };
  const save = vi.fn().mockResolvedValue(policy);
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
      { ad_formats: null, promotion_surfaces: null },
      expect.any(AbortSignal),
    ),
  );
});

it("retains product edits on a failed save and supports retry without widening them", async () => {
  const policy: ProductPlacementPolicy = {
    excluded: false,
    ad_formats: [],
    promotion_surfaces: [],
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

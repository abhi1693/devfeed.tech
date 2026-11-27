// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { generateMetadata } from "@/app/layout";

vi.mock("@devfeed/theme/assets/devfeed-icon-32.png", () => ({
  default: { src: "/assets/favicon-32.png" },
}));
vi.mock("@devfeed/theme/assets/devfeed-icon-180.png", () => ({
  default: { src: "/assets/apple-180.png" },
}));

vi.mock("next/server", () => ({ connection: vi.fn() }));
it("uses compact icons while keeping admin pages out of search results", async () => {
  const metadata = await generateMetadata();
  expect(metadata.icons).toEqual({
    icon: { url: "/assets/favicon-32.png", type: "image/png", sizes: "32x32" },
    apple: { url: "/assets/apple-180.png", type: "image/png", sizes: "180x180" },
  });
  expect(metadata.robots).toEqual({ index: false, follow: false });
  expect(metadata.title).toEqual({
    default: "DevFeed Admin",
    template: "%s · DevFeed Admin",
  });
});

it("brands Hardware Today admin metadata independently", async () => {
  vi.stubEnv("DEVFEED_PRODUCT", "hardwaredaily");
  try {
    expect((await generateMetadata()).title).toEqual({
      default: "Hardware Today Admin",
      template: "%s · Hardware Today Admin",
    });
  } finally {
    vi.unstubAllEnvs();
  }
});

it("keeps client-side record titles aligned with the rendered deployment", async () => {
  const { pageTitle } = await import("@/lib/page-titles");
  const { productWordmark } = await import("@devfeed/theme/product");
  document.documentElement.dataset.product = "hardwaredaily";
  try {
    expect(productWordmark()).toBe("Hardware Today");
    expect(pageTitle("  Sources  ")).toBe("Sources · Hardware Today Admin");
  } finally {
    delete document.documentElement.dataset.product;
  }
  expect(productWordmark()).toBe("devfeed.");
});

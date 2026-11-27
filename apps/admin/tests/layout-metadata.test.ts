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

it("brands Hardware Daily admin metadata independently", async () => {
  vi.stubEnv("DEVFEED_PRODUCT", "hardwaredaily");
  try {
    expect((await generateMetadata()).title).toEqual({
      default: "Hardware Daily Admin",
      template: "%s · Hardware Daily Admin",
    });
  } finally {
    vi.unstubAllEnvs();
  }
});

import { expect, it, vi } from "vitest";
import { metadata } from "@/app/layout";

vi.mock("@devfeed/theme/assets/devfeed-icon-32.png", () => ({
  default: { src: "/assets/favicon-32.png" },
}));
vi.mock("@devfeed/theme/assets/devfeed-icon-180.png", () => ({
  default: { src: "/assets/apple-180.png" },
}));

it("uses compact icons while keeping admin pages out of search results", () => {
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

import { afterEach, expect, it, vi } from "vitest";
import { connection } from "next/server";
import { generateMetadata } from "@/app/layout";

vi.mock("next/server", () => ({ connection: vi.fn() }));
vi.mock("@devfeed/theme/assets/devfeed-icon-32.png", () => ({
  default: { src: "/assets/favicon-32.png" },
}));
vi.mock("@devfeed/theme/assets/devfeed-icon-180.png", () => ({
  default: { src: "/assets/apple-180.png" },
}));

afterEach(() => vi.unstubAllEnvs());
it("uses compact icons and request-specific deployment metadata", async () => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://reader.example");
  const metadata = await generateMetadata();
  expect(connection).toHaveBeenCalled();
  expect(metadata.metadataBase).toEqual(new URL("https://reader.example/"));
  expect(metadata.icons).toEqual({
    icon: { url: "/assets/favicon-32.png", type: "image/png", sizes: "32x32" },
    apple: { url: "/assets/apple-180.png", type: "image/png", sizes: "180x180" },
  });
  expect(metadata.title).toEqual({
    default: "DevFeed — Developer news",
    template: "%s · DevFeed",
  });
  expect(metadata.robots).toEqual({ "max-image-preview": "large" });
});

it("uses the Hardware Daily icons and social artwork for that deployment", async () => {
  vi.stubEnv("DEVFEED_PRODUCT", "hardwaredaily");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://hardware.example");
  const metadata = await generateMetadata();
  expect(metadata.icons).toMatchObject({
    icon: { url: "/hardware-daily/icon-32.png" },
    apple: { url: "/hardware-daily/icon-180.png" },
  });
  expect(metadata.openGraph).toMatchObject({
    siteName: "Hardware Daily",
    images: [
      { url: "https://hardware.example/hardware-daily/opengraph.jpg", width: 1200, height: 630 },
    ],
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";
import { connection } from "next/server";
import RootLayout, { generateMetadata } from "@/app/layout";

vi.mock("@devfeed/theme/assets/devfeed-mark.png", () => ({ default: { src: "/assets/mark.png" } }));
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

it("uses the Hardware Today icons and social artwork for that deployment", async () => {
  vi.stubEnv("DEVFEED_PRODUCT", "hardwaredaily");
  vi.stubEnv("DEVFEED_USER_BASE_URL", "https://hardware.example");
  const metadata = await generateMetadata();
  expect(metadata.icons).toMatchObject({
    icon: { url: "/hardware-daily/icon-32.png" },
    apple: { url: "/hardware-daily/icon-180.png" },
  });
  expect(metadata.openGraph).toMatchObject({
    siteName: "Hardware Today",
    images: [
      { url: "https://hardware.example/hardware-daily/opengraph.jpg", width: 1200, height: 630 },
    ],
  });
});

vi.mock("next/headers", () => ({
  headers: async () => new Headers({ "content-security-policy": "script-src 'nonce-request123'" }),
}));

it.each([
  ["hardwaredaily", "https://hardwaretoday.app", "GTM-PTZFLDVG", true],
  ["devfeed", "https://devfeed.tech", "GTM-PTZFLDVG", false],
  ["hardwaredaily", "https://preview.example", "GTM-PTZFLDVG", false],
  ["hardwaredaily", "https://hardwaretoday.app", "G-INVALID", false],
])(
  "scopes the GTM loader to the Hardware Today site (%s, %s, %s)",
  async (product, origin, id, expected) => {
    vi.stubEnv("DEVFEED_PRODUCT", String(product));
    vi.stubEnv("DEVFEED_USER_BASE_URL", String(origin));
    vi.stubEnv("GOOGLE_TAG_MANAGER_ID", String(id));
    const layout = await RootLayout({ children: null, modal: null });
    const markup = renderToStaticMarkup(layout.props.children[0]);
    expect(markup.includes("googletagmanager.com/gtm.js?id=GTM-PTZFLDVG")).toBe(expected);
    if (expected) {
      expect(markup).toContain("gtm.start");
      expect(markup).toContain('nonce="request123"');
    }
  },
);

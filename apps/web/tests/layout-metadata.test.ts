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

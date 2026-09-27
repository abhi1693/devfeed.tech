import { beforeEach, expect, it, vi } from "vitest";
vi.mock("@/lib/api", () => ({
  getTopic: vi.fn(),
  UserApiError: class extends Error {
    status = 404;
  },
}));
vi.mock("@/lib/server/card-image", () => ({ cardImage: vi.fn() }));
import { getTopic } from "@/lib/api";
import { cardImage } from "@/lib/server/card-image";
import { GET } from "@/app/api/v1/topics/[slug]/logo/route";

beforeEach(() => vi.clearAllMocks());
it("exports only the catalog logo through the bounded image sanitizer", async () => {
  vi.mocked(getTopic).mockResolvedValue({ logo_url: "https://www.rancher.com/logo.svg" } as never);
  vi.mocked(cardImage).mockResolvedValue("data:image/webp;base64,AAAA");
  const request = new Request(
    "https://devfeed.tech/api/v1/topics/rancher/logo?url=http://127.0.0.1/private",
  );
  const response = await GET(request, { params: Promise.resolve({ slug: "rancher" }) });
  expect(getTopic).toHaveBeenCalledWith("rancher", request.signal);
  expect(cardImage).toHaveBeenCalledWith("https://www.rancher.com/logo.svg", "logo");
  expect(await response.json()).toEqual({ image: "data:image/webp;base64,AAAA" });
  expect(response.headers.get("cache-control")).toBe("no-store");
});
it("rejects invalid slugs before any fetch and handles missing or unsafe images", async () => {
  expect(
    (
      await GET(new Request("https://devfeed.tech"), {
        params: Promise.resolve({ slug: "../private" }),
      })
    ).status,
  ).toBe(404);
  expect(getTopic).not.toHaveBeenCalled();
  vi.mocked(getTopic).mockResolvedValue({ logo_url: "http://127.0.0.1/private" } as never);
  vi.mocked(cardImage).mockResolvedValue(null);
  expect(
    (
      await GET(new Request("https://devfeed.tech"), {
        params: Promise.resolve({ slug: "rancher" }),
      })
    ).status,
  ).toBe(404);
});

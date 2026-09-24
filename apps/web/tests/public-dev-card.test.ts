import { afterEach, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("@/lib/server/config", () => ({ userApiOrigin: () => "http://user-api.test" }));
import { publicDevCard } from "@/lib/server/public-dev-card";
afterEach(() => vi.unstubAllGlobals());
it("loads only the public endpoint without forwarding credentials or caching", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ username: "reader", display_name: "Reader" }));
  vi.stubGlobal("fetch", fetcher);
  expect(await publicDevCard("reader")).toEqual({ username: "reader", display_name: "Reader" });
  expect(fetcher.mock.calls[0][0]).toBe("http://user-api.test/v1/user/profiles/reader");
  expect(fetcher.mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  expect(fetcher.mock.calls[0][1].headers).toBeUndefined();
});
it.each(["../settings/profile", "a", "a".repeat(31), "reader?private=true"])(
  "rejects unsafe usernames: %s",
  async (username) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    expect(await publicDevCard(username)).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  },
);
it("returns no card when public access is revoked", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
  expect(await publicDevCard("reader")).toBeNull();
});
it("does not disguise service failures as missing profiles", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
  await expect(publicDevCard("reader")).rejects.toThrow("temporarily unavailable");
});

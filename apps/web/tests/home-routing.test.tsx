import { beforeEach, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { hasUserSession } from "@/lib/api";
vi.mock("@/lib/api", () => ({ hasUserSession: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`REDIRECT:${path}`);
  },
}));
vi.mock("@/components/user-shell", () => ({ UserShell: () => null }));
vi.mock("@/components/personal-feed", () => ({ PersonalFeed: () => null }));
beforeEach(() => vi.clearAllMocks());
it("redirects anonymous visitors to latest", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(false);
  await expect(Home({ searchParams: Promise.resolve({}) })).rejects.toThrow("REDIRECT:/latest");
});
it("renders personal feed for a verified session", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(true);
  expect(await Home({ searchParams: Promise.resolve({ cursor: "next" }) })).toBeTruthy();
});
it("does not treat account outages as signed-out sessions", async () => {
  vi.mocked(hasUserSession).mockRejectedValue(new Error("account unavailable"));
  await expect(Home({ searchParams: Promise.resolve({}) })).rejects.toThrow("account unavailable");
});
it("keeps signed-in visitors on My feed even with unrelated query parameters", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(true);
  expect(await Home({ searchParams: Promise.resolve({ language: "fr" }) })).toBeTruthy();
});

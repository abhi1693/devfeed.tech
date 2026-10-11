import { beforeEach, expect, it, vi } from "vitest";
import Home, { generateMetadata } from "@/app/page";
import { FeedView } from "@/components/feed-view";
import { hasUserSession } from "@/lib/api";
vi.mock("@/lib/api", () => ({ hasUserSession: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error(`REDIRECT:${path}`);
  },
}));
vi.mock("@/components/feed-view", () => ({ FeedView: vi.fn(async () => "guest feed") }));
vi.mock("@/components/user-shell", () => ({ UserShell: () => null }));
vi.mock("@/components/personal-feed", () => ({ PersonalFeed: () => null }));
beforeEach(() => vi.clearAllMocks());
it("renders the guest feed directly at root with the latest guest defaults", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(false);
  expect(await Home({ searchParams: Promise.resolve({}) })).toBe("guest feed");
  expect(FeedView).toHaveBeenCalledWith({
    filters: expect.objectContaining({ content_type: "article" }),
  });
});
it("preserves guest filters and cursors while ignoring attribution as API filters", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(false);
  await Home({
    searchParams: Promise.resolve({
      q: "engineering",
      topic: "python",
      cursor: "next",
      utm_source: "linkedin",
    }),
  });
  expect(FeedView).toHaveBeenCalledWith({
    filters: expect.objectContaining({
      q: "engineering",
      topic: "python",
      cursor: "next",
      content_type: "",
    }),
  });
  expect(vi.mocked(FeedView).mock.calls[0][0].filters).not.toHaveProperty("utm_source");
});
it("uses existing public canonical URLs for guests and private metadata for members", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(false);
  const metadata = await generateMetadata({
    searchParams: Promise.resolve({ utm_source: "linkedin" }),
  });
  expect(metadata.alternates?.canonical).toBe("https://devfeed.tech/latest");
  expect(metadata.robots).toBeUndefined();
  vi.mocked(hasUserSession).mockResolvedValue(true);
  expect(await generateMetadata({ searchParams: Promise.resolve({}) })).toEqual({
    title: "My feed",
    robots: { index: false, follow: false },
  });
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

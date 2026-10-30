import { beforeEach, expect, it, vi } from "vitest";
import Home, { generateMetadata } from "@/app/page";
import { GuestHomeIntro } from "@/components/guest-home-intro";
import { PublicPage } from "@/components/public-page";
import { FeedView } from "@/components/feed-view";
import { hasUserSession } from "@/lib/api";
import { readerFeeds } from "@/lib/reader-feed";
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
it.each([{}, { content_type: "" }, { content_type: "invalid" }, { q: "engineering" }])(
  "renders All at the guest root without an implicit article filter: %j",
  async (query) => {
    vi.mocked(hasUserSession).mockResolvedValue(false);
    const page = await Home({ searchParams: Promise.resolve(query) });
    expect(page.type).toBe(PublicPage);
    expect(await page.props.content).toBe("guest feed");
    expect(FeedView).toHaveBeenCalledWith(
      expect.objectContaining({
        feedDescriptor: readerFeeds.home,
        withShell: false,
        filters: expect.objectContaining({ content_type: "" }),
      }),
    );
  },
);
it.each(["article", "news"])(
  "preserves the explicit guest %s content-type selection",
  async (type) => {
    vi.mocked(hasUserSession).mockResolvedValue(false);
    await Home({ searchParams: Promise.resolve({ content_type: type, cursor: "next" }) });
    expect(FeedView).toHaveBeenCalledWith({
      feedDescriptor: readerFeeds.home,
      withShell: false,
      filters: expect.objectContaining({ content_type: type, cursor: "next" }),
    });
  },
);
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
    feedDescriptor: readerFeeds.home,
    withShell: false,
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
  expect(metadata.title).toBe("Developer news, tutorials & releases");
  expect(metadata.robots).toBeUndefined();
  vi.mocked(hasUserSession).mockResolvedValue(true);
  expect(await generateMetadata({ searchParams: Promise.resolve({}) })).toEqual({
    title: "My feed",
    robots: { index: false, follow: false },
  });
});
it("renders personal feed with the home descriptor and normalized cursor for a verified session", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(true);
  const page = await Home({ searchParams: Promise.resolve({ cursor: [" next ", "ignored"] }) });
  expect(page).toMatchObject({
    props: {
      section: "personal",
      children: {
        props: {
          feedDescriptor: readerFeeds.home,
          cursor: "next",
          filters: expect.objectContaining({ cursor: "next", content_type: "" }),
        },
      },
    },
  });
  expect(FeedView).not.toHaveBeenCalled();
});
it("does not treat account outages as signed-out sessions", async () => {
  vi.mocked(hasUserSession).mockRejectedValue(new Error("account unavailable"));
  await expect(Home({ searchParams: Promise.resolve({}) })).rejects.toThrow("account unavailable");
});
it("keeps signed-in visitors on My feed even with unrelated query parameters", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(true);
  expect(await Home({ searchParams: Promise.resolve({ language: "fr" }) })).toBeTruthy();
});

it("returns the guest shell while its fresh feed is still pending", async () => {
  vi.mocked(hasUserSession).mockResolvedValue(false);
  let release!: (value: Awaited<ReturnType<typeof FeedView>>) => void;
  const content = new Promise<Awaited<ReturnType<typeof FeedView>>>((resolve) => {
    release = resolve;
  });
  vi.mocked(FeedView).mockReturnValueOnce(content);
  const page = await Home({ searchParams: Promise.resolve({}) });
  expect(page.type).toBe(PublicPage);
  expect(page.props.content).toBe(content);
  expect(page.props.children.type).toBe(GuestHomeIntro);
  expect(FeedView).toHaveBeenCalledWith(expect.objectContaining({ headingLevel: 2 }));
  const fresh = <p>Fresh feed</p>;
  release(fresh);
  expect(await page.props.content).toBe(fresh);
});

it.each([
  [{ utm_source: "reader" }, true],
  [{ q: "engineering" }, false],
  [{ topic: "python" }, false],
  [{ cursor: "next" }, false],
  [{ content_type: "news" }, false],
  [{ sort: "oldest" }, false],
] as const)(
  "keeps guest introduction eligibility separate from feed data: %j",
  async (query, intro) => {
    vi.mocked(hasUserSession).mockResolvedValue(false);
    const page = await Home({ searchParams: Promise.resolve(query) });
    expect(Boolean(page.props.children)).toBe(intro);
    expect(vi.mocked(FeedView).mock.calls[0][0].headingLevel).toBe(intro ? 2 : undefined);
  },
);

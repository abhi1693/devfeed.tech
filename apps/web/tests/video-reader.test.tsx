// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ArticleCard } from "@/components/article-card";
import { ArticlePreviewContent } from "@/components/article-preview-content";
import { ArticleTable } from "@/components/article-table";
import { VideoMetadata } from "@/components/video-metadata";
import { SearchResults } from "@/components/search-results";
import { articleAction, canFollowSource, sourceCollectionLabel, videoDuration } from "@/lib/video";
import { articleStructuredData } from "@/lib/structured-data";
import type { Article, Source } from "@/lib/types";
import { article, source } from "./fixtures";

vi.mock("@/components/user-account", () => ({ useUser: () => ({ user: null, loading: false }) }));
vi.mock("@/components/source-follow", () => ({ SourceFollow: () => null }));
vi.mock("@/components/partner-placements", () => ({
  PartnerPlacements: ({ children }: { children?: (cards: ReactNode[]) => ReactNode }) =>
    children?.([]) ?? null,
}));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: vi.fn(async () => []),
}));

const channel: Source = {
  ...source,
  provider: "youtube",
  resource_kind: "channel",
  provider_resource_id: "UC0123456789012345678901",
  recurring: true,
  name: "Systems Engineering",
  slug: "systems-channel",
  website_url: "https://www.youtube.com/channel/UC0123456789012345678901",
};
const playlist: Source = {
  ...channel,
  id: "playlist-source",
  slug: "systems-playlist",
  name: "Concurrency talks",
  resource_kind: "playlist",
};
const video: Article = {
  ...article,
  provider: "youtube",
  provider_resource_id: "dQw4w9WgXcQ",
  content_format: "video",
  canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "Thread safety in developer tools",
  sources: [channel, playlist],
  video_metadata: {
    channel_id: channel.provider_resource_id,
    channel_title: channel.name,
    channel_url: channel.website_url,
    duration_seconds: 3723,
    availability: "public",
    retrieved_at: "2026-10-16T00:00:00Z",
  },
};
afterEach(cleanup);

it.each([
  [0, "0:00"],
  [59, "0:59"],
  [60, "1:00"],
  [3600, "1:00:00"],
  [3723, "1:02:03"],
  [null, null],
  [undefined, null],
  [-1, null],
  [1.5, null],
  [NaN, null],
])("formats known video duration %s", (seconds, expected) => {
  expect(videoDuration(seconds)).toBe(expected);
});

it("keeps the canonical YouTube action while preserving ordinary article links", () => {
  expect(articleAction(video)).toEqual({
    href: video.canonical_url,
    label: "Watch on YouTube",
    accessibleLabel: "Watch on YouTube in a new tab",
  });
  expect(articleAction(article).label).toBe("Read");
  expect(articleAction(article).href).toContain("utm_source=devfeed");
  expect(
    articleAction({ ...video, canonical_url: "https://youtube.com.evil.example/watch?v=private" })
      .label,
  ).toBe("Watch video");
});

it("shows video identity, duration, channel attribution, and accessible watch actions on cards", () => {
  render(<ArticleCard article={video} />);
  const watch = screen.getByRole("link", { name: "Watch on YouTube in a new tab" });
  expect(watch.getAttribute("href")).toBe(video.canonical_url);
  expect(watch.getAttribute("target")).toBe("_blank");
  expect(screen.getByText("Video")).toBeTruthy();
  expect(screen.getByLabelText("Video duration: 1 hour, 2 minutes, 3 seconds").textContent).toBe(
    "1:02:03",
  );
  expect(
    within(screen.getByLabelText("Video details"))
      .getByRole("link", { name: channel.name })
      .getAttribute("href"),
  ).toBe(channel.website_url);
  expect(screen.getByRole("link", { name: /Sign in to like this video/ })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in to save video for later" })).toBeTruthy();
});

it("retains video context and its stable preview route in compact rows", () => {
  render(<ArticleTable articles={[video]} showHeader />);
  expect(screen.getByRole("link", { name: video.title }).getAttribute("href")).toBe(
    `/articles/${video.slug}`,
  );
  expect(screen.getByText("Video")).toBeTruthy();
  expect(screen.getByText("1:02:03")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Sign in to save video for later" })).toBeTruthy();
});

it("shows all collection origins in the preview and states what an open measures", () => {
  const { container } = render(<ArticlePreviewContent article={video} />);
  expect(
    screen.getByRole("link", { name: "Watch on YouTube in a new tab" }).getAttribute("href"),
  ).toBe(video.canonical_url);
  expect(screen.getByText(/Opening a link does not confirm playback or completion/)).toBeTruthy();
  expect(container.querySelectorAll(".source-brief")).toHaveLength(2);
  expect(screen.getByText("YouTube channel")).toBeTruthy();
  expect(screen.getByText("YouTube playlist")).toBeTruthy();
  expect(
    within(container.querySelectorAll(".source-brief")[1] as HTMLElement)
      .getByRole("link", { name: playlist.name })
      .getAttribute("href"),
  ).toBe(`/sources/${playlist.slug}`);
  expect(screen.getByRole("button", { name: `Share video: ${video.title}` })).toBeTruthy();
});

it("shows search video metadata without changing the preview destination", () => {
  render(
    <SearchResults
      result={{
        query: "threads",
        sections: {
          articles: {
            items: [
              {
                id: video.id,
                title: video.title,
                description: video.summary,
                href: `/articles/${video.slug}`,
                image_url: null,
                published_at: video.published_at,
                label: "tutorial",
                content_format: "video",
                provider: "youtube",
                video_metadata: video.video_metadata,
              },
            ],
            next_cursor: null,
          },
        },
      }}
    />,
  );
  expect(screen.getByRole("link", { name: video.title }).getAttribute("href")).toBe(
    `/articles/${video.slug}`,
  );
  expect(screen.getByText("Video")).toBeTruthy();
  expect(screen.getByText("1:02:03")).toBeTruthy();
});

it("does not invent missing duration or expose an unsafe channel URL", () => {
  render(
    <VideoMetadata
      content={{
        ...video,
        video_metadata: {
          channel_title: "<unsafe>",
          channel_url: "javascript:alert(1)",
          duration_seconds: null,
        },
      }}
    />,
  );
  expect(screen.getByText("<unsafe>")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.queryByRole("time")).toBeNull();
});

it("publishes evidence-backed VideoObject metadata rather than relabeling a video as Article", () => {
  const data = articleStructuredData(video);
  expect(data.mainEntity).toMatchObject({
    "@type": "VideoObject",
    name: video.title,
    uploadDate: new Date(video.published_at!).toISOString(),
    duration: "PT3723S",
    creator: { name: channel.name },
  });
  expect(sourceCollectionLabel(channel)).toBe("YouTube channel");
  expect(sourceCollectionLabel(playlist)).toBe("YouTube playlist");
  expect(sourceCollectionLabel(source)).toBeNull();
});

it("follows recurring YouTube collections but never a one-off video origin", () => {
  expect(canFollowSource(channel)).toBe(true);
  expect(canFollowSource(playlist)).toBe(true);
  expect(canFollowSource(source)).toBe(true);
  expect(canFollowSource({ ...channel, resource_kind: "video" })).toBe(false);
});

import { outboundArticleUrl, safeExternalUrl } from "./feed-query";
import type { Article, Source, VideoMetadata } from "./types";

export type VideoContent = {
  content_format?: string | null;
  provider?: string | null;
  video_metadata?: VideoMetadata | null;
};
export const isVideo = (content: VideoContent) => content.content_format === "video";
export function youtubeUrl(value: string | null | undefined) {
  const safe = safeExternalUrl(value);
  if (!safe) return undefined;
  const url = new URL(safe);
  return url.protocol === "https:" &&
    ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"].includes(url.hostname)
    ? url.href
    : undefined;
}
export function articleAction(
  article: Pick<Article, "canonical_url" | "content_format" | "provider">,
) {
  const youtube =
    isVideo(article) && article.provider === "youtube" && youtubeUrl(article.canonical_url);
  return {
    href: youtube || outboundArticleUrl(article.canonical_url),
    label: youtube ? "Watch on YouTube" : isVideo(article) ? "Watch video" : "Read",
    accessibleLabel: youtube
      ? "Watch on YouTube in a new tab"
      : isVideo(article)
        ? "Watch original video in a new tab"
        : "Read original article in a new tab",
  };
}
export function videoDuration(seconds: number | null | undefined) {
  if (!Number.isSafeInteger(seconds) || Number(seconds) < 0) return null;
  const value = Number(seconds);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remainder = value % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}
export function videoDurationLabel(seconds: number) {
  const parts = [
    [Math.floor(seconds / 3600), "hour"],
    [Math.floor((seconds % 3600) / 60), "minute"],
    [seconds % 60, "second"],
  ] as const;
  return (
    parts
      .filter(([value]) => value > 0)
      .map(([value, unit]) => `${value} ${unit}${value === 1 ? "" : "s"}`)
      .join(", ") || "0 seconds"
  );
}
export function sourceCollectionLabel(
  source: Pick<Source, "provider" | "resource_kind"> | undefined,
) {
  if (source?.provider !== "youtube") return null;
  return source.resource_kind === "channel"
    ? "YouTube channel"
    : source.resource_kind === "playlist"
      ? "YouTube playlist"
      : source.resource_kind === "video"
        ? "YouTube video"
        : "YouTube";
}

export const canFollowSource = (source: Pick<Source, "provider" | "resource_kind"> | undefined) =>
  source?.provider !== "youtube" || source.resource_kind !== "video";

import type { Metadata } from "next";
import { feedHref, parseFilters, type FeedFilters, type SearchParams } from "./feed-query";
import { catalogOffset } from "./catalog-page";
import { safeExternalUrl, contentTypeRoutes, contentTypes } from "./feed-query";
import type { Article } from "./types";
import { publicSiteOrigin } from "./server/config";

export function canonicalUrl(path: string) {
  if (!path.startsWith("/") || path.startsWith("//") || /[\\#]/.test(path))
    throw new Error("Canonical path must be a local URL without a fragment");
  return new URL(path, publicSiteOrigin()).href;
}

export function feedCanonical(query: SearchParams, scope: Partial<FeedFilters> = {}) {
  const filters = { ...parseFilters({ ...query, ...scope }), source_slug: scope.source_slug };
  return canonicalUrl(feedHref(filters, { cursor: filters.cursor }));
}

export function catalogCanonical(path: string, query: SearchParams) {
  const offset = catalogOffset(query.offset);
  return canonicalUrl(path + (offset ? `?offset=${offset}` : ""));
}

export function feedMetadata(
  title: string,
  description: string,
  query: SearchParams,
  scope: Partial<FeedFilters> = {},
): Metadata {
  const canonical = feedCanonical(query, scope);
  const refined = Boolean(new URL(canonical).search);
  const type = contentTypes.find((value) => value === scope.content_type);
  const scoped = scope.topic || scope.source_id || scope.tag;
  const pageTitle = scoped && type ? `${title} ${contentTypeRoutes[type]}` : title;
  return {
    ...pageMetadata(pageTitle, description, canonical),
    ...(refined ? { robots: { index: false, follow: true } } : {}),
  };
}

export const SITE_DESCRIPTION =
  "Learn daily. Build better. Stay ahead. Discover developer news, tutorials, and releases organized by topic and source.";

export function socialImage() {
  return {
    url: canonicalUrl("/opengraph.png"),
    width: 1731,
    height: 909,
    type: "image/png",
    alt: "DevFeed — Learn daily. Build better. Stay ahead.",
  };
}

export function socialMetadata(title: string, description: string): Metadata {
  const image = socialImage();
  const socialTitle = title.startsWith("DevFeed") ? title : `${title} · DevFeed`;
  return {
    openGraph: {
      type: "website",
      siteName: "DevFeed",
      locale: "en_US",
      title: socialTitle,
      description,
      images: [image],
    },
    twitter: {
      card: "summary_large_image",
      title: socialTitle,
      description,
      images: [{ url: image.url, alt: image.alt }],
    },
  };
}

export function pageMetadata(title: string, description: string, canonical: string): Metadata {
  const social = socialMetadata(title, description);
  return {
    title,
    description,
    alternates: { canonical },
    ...social,
    openGraph: { ...social.openGraph, url: canonical },
  };
}

export function articleDescription(article: Article): string {
  // Match the visible overview. Remove common Markdown presentation from the short preview.
  return (article.ai_summary || article.summary || article.title)
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]*>/g, " ")
    .replace(/(^|\n)\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

export function articleMetadata(article: Article): Metadata {
  const metadata = pageMetadata(
    article.title,
    articleDescription(article),
    canonicalUrl(`/articles/${encodeURIComponent(article.slug)}`),
  );
  const cover = safeExternalUrl(article.image_url);
  const image = cover ? { url: cover, alt: article.title } : socialImage();
  const published = validDate(article.published_at);
  return {
    ...metadata,
    openGraph: {
      ...metadata.openGraph,
      type: "article",
      ...(published ? { publishedTime: published } : {}),
      section: article.content_type,
      tags: article.tags,
      images: [image],
    },
    twitter: {
      ...metadata.twitter,
      card: "summary_large_image",
      images: [{ url: image.url, alt: image.alt }],
    },
  };
}

export function validDate(value: string | null): string | undefined {
  if (!value || Number.isNaN(Date.parse(value))) return undefined;
  return new Date(value).toISOString();
}

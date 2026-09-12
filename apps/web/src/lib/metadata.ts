import type { Metadata } from "next";
import { feedHref, parseFilters, type FeedFilters, type SearchParams } from "./feed-query";
import { catalogOffset } from "./catalog-page";
import { publicSiteOrigin } from "./server/config";

export function canonicalUrl(path: string) {
  if (!path.startsWith("/") || path.startsWith("//") || /[\\#]/.test(path))
    throw new Error("Canonical path must be a local URL without a fragment");
  return new URL(path, publicSiteOrigin()).href;
}

export function feedCanonical(query: SearchParams, scope: Partial<FeedFilters> = {}) {
  const filters = parseFilters({ ...query, ...scope });
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
  return {
    title,
    description,
    alternates: { canonical },
    ...(refined ? { robots: { index: false, follow: true } } : {}),
  };
}

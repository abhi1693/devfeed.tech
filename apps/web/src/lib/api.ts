import { searchOptionParams, type SearchOptions } from "./search";
import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { ReaderCookies } from "./server/reader-cookies";
import { xPixelEnabled } from "./server/config";
import type { Article, FeedPage, FeedOptions, Source, Topic, Tag } from "./types";
import { privateApi, publicApi, UserApiError } from "./server/api-client";
import { contentTypes, feedParams, latestFeedParams, type FeedFilters } from "./feed-query";

const readerCookies = new ReaderCookies(xPixelEnabled);

export { UserApiError } from "./server/api-client";
export async function hasUserSession(): Promise<boolean> {
  const cookie = readerCookies.filter((await cookies()).toString(), "reader");
  if (!/(?:^|; )(?:__Host-)?devfeed_user_session=/.test(cookie)) return false;
  try {
    const user = await privateApi.read<{ user_id: string } | null>("/v1/user/auth/me", { cookie });
    return Boolean(user?.user_id);
  } catch (error) {
    if (error instanceof UserApiError && error.status === 401) return false;
    throw error;
  }
}

const resolveFeedPreferences = cache(async (cookie: string, signal?: AbortSignal) => {
  const defaults = { content_types: [...contentTypes] as string[], languages: ["en"] };
  if (!/(?:^|; )(?:__Host-)?devfeed_user_session=/.test(cookie)) return defaults;
  try {
    const settings = await privateApi.read<{ content_types: string[]; languages?: string[] }>(
      "/v1/user/settings/feed",
      { signal, cookie },
    );
    if (
      !Array.isArray(settings.content_types) ||
      !settings.content_types.length ||
      settings.content_types.some((type) => !contentTypes.some((known) => known === type))
    )
      throw new UserApiError(502);
    const languages = settings.languages ?? ["en"];
    if (
      !Array.isArray(languages) ||
      !languages.length ||
      languages.some((code) => !/^[a-z]{2,3}$/.test(code))
    )
      throw new UserApiError(502);
    return { content_types: settings.content_types, languages };
  } catch (error) {
    if (error instanceof UserApiError && error.status === 401) return defaults;
    throw error;
  }
});
export async function getReaderFeedPreferences(signal?: AbortSignal, cookieHeader?: string) {
  return resolveFeedPreferences(
    readerCookies.filter(cookieHeader ?? (await cookies()).toString(), "reader"),
    signal,
  );
}
// Metadata and page content share one snapshot during a React server render.
// This cache ends with the request; the API still validates visibility on every new request.
const renderFeed = cache((path: string) => publicApi.read<FeedPage>(path));
export async function getFeed(filters: FeedFilters, signal?: AbortSignal, cookieHeader?: string) {
  const params = latestFeedParams(filters);
  params.set("limit", "24");
  params.delete("language");
  const settings = await getReaderFeedPreferences(signal, cookieHeader);
  for (const language of settings.languages) params.append("languages", language);
  if (!filters.content_type && settings.content_types.length < contentTypes.length)
    for (const type of settings.content_types) params.append("content_types", type);
  const path = `/v1/feed?${params}`;
  // Independently cancellable reads must retain their own transport lifetime.
  return signal ? publicApi.read<FeedPage>(path, { signal }) : renderFeed(path);
}
export async function getFeedOptions(
  filters: FeedFilters,
  signal?: AbortSignal,
  cookieHeader?: string,
) {
  const params = feedParams({ ...filters, cursor: "", sort: "" });
  params.delete("language");
  const settings = await getReaderFeedPreferences(signal, cookieHeader);
  for (const language of settings.languages) params.append("languages", language);
  return publicApi.read<FeedOptions>(`/v1/feed/options?${params}`, { signal });
}
export const getTopics = async (
  offset = 0,
  limit = 60,
  signal?: AbortSignal,
  sort: "name" | "articles" = "name",
  query = "",
  cookieHeader?: string,
) => {
  const settings = await getReaderFeedPreferences(signal, cookieHeader);
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    has_articles: "true",
  });
  for (const language of settings.languages) params.append("languages", language);
  if (sort !== "name") params.set("sort", sort);
  if (query) params.set("q", query);
  return publicApi.read<Topic[]>(`/v1/topics?${params}`, { signal });
};
export const getTopic = (slug: string, signal?: AbortSignal, byId = false) =>
  publicApi.read<Topic>(`/v1/topics/${encodeURIComponent(slug)}${byId ? "?by_id=true" : ""}`, {
    signal,
  });
export const getSources = async (
  offset = 0,
  limit = 60,
  signal?: AbortSignal,
  query = "",
  cookieHeader?: string,
) => {
  const settings = await getReaderFeedPreferences(signal, cookieHeader);
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    enabled: "true",
    has_articles: "true",
  });
  for (const language of settings.languages) params.append("languages", language);
  if (query) params.set("q", query);
  return publicApi.read<Source[]>(`/v1/sources?${params}`, { signal });
};
export const getSource = (id: string, signal?: AbortSignal) =>
  publicApi.read<Source>(`/v1/sources/${encodeURIComponent(id)}`, { signal });
export const getArticle = (slug: string, signal?: AbortSignal) =>
  publicApi.read<Article>(`/v1/articles/${encodeURIComponent(slug)}`, { signal });

export async function getTrending(cursor = "") {
  return privateApi.read<FeedPage>(
    `/v1/user/trending?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
  );
}

export async function getSearch(
  query: string,
  section?: string,
  page = "1",
  signal?: AbortSignal,
  options?: SearchOptions,
) {
  const params = searchOptionParams(options);
  params.set("q", query);
  params.set("page", page);
  if (section) params.set("section", section);
  const deadline = AbortSignal.timeout(3500);
  return publicApi.read<import("./search").SearchResponse>(`/v1/search?${params}`, {
    signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
  });
}

export const getTag = (slug: string, signal?: AbortSignal, byId = false) =>
  publicApi.read<Tag>(`/v1/tags/${encodeURIComponent(slug)}${byId ? "?by_id=true" : ""}`, {
    signal,
  });
export const getTags = (offset = 0, limit = 60, query = "", letter = "") =>
  publicApi.read<Tag[]>(
    `/v1/tags?${new URLSearchParams({ limit: String(limit), offset: String(offset), q: query, letter })}`,
  );

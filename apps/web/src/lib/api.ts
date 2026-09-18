import { searchOptionParams, type SearchOptions } from "./search";
import { traceHeaders } from "@devfeed/telemetry/propagation";
import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { userCookies } from "./server/gateway";
import { userApiOrigin } from "./server/config";
import type { Article, FeedPage, FeedOptions, Source, Topic } from "./types";
import { publicReadCacheControl } from "./public-read-cache";
import { contentTypes, feedParams, latestFeedParams, type FeedFilters } from "./feed-query";

export class UserApiError extends Error {
  constructor(public status: number) {
    super("The requested service is unavailable");
  }
}
async function read<T>(
  path: string,
  origin = process.env.DEVFEED_PUBLIC_API_URL ?? "http://127.0.0.1:8000",
  signal?: AbortSignal,
  cookie?: string,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(new URL(path, origin), {
      cache: "no-store",
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(8000)])
        : AbortSignal.timeout(8000),
      headers: {
        Accept: "application/json",
        ...(!path.startsWith("/v1/user/") && !cookie
          ? { "Cache-Control": publicReadCacheControl }
          : {}),
        ...(cookie ? { Cookie: cookie } : {}),
        ...traceHeaders(),
      },
      redirect: "error",
    });
  } catch {
    throw new UserApiError(503);
  }
  if (!response.ok) throw new UserApiError(response.status);
  try {
    return (await response.json()) as T;
  } catch {
    throw new UserApiError(502);
  }
}
export async function hasUserSession(): Promise<boolean> {
  const cookie = userCookies((await cookies()).toString());
  if (!/(?:^|; )(?:__Host-)?devfeed_user_session=/.test(cookie)) return false;
  try {
    const user = await read<{ user_id: string } | null>(
      "/v1/user/auth/me",
      userApiOrigin(),
      undefined,
      cookie,
    );
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
    const settings = await read<{ content_types: string[]; languages?: string[] }>(
      "/v1/user/settings/feed",
      userApiOrigin(),
      signal,
      cookie,
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
  return resolveFeedPreferences(userCookies(cookieHeader ?? (await cookies()).toString()), signal);
}
export async function getFeed(filters: FeedFilters, signal?: AbortSignal, cookieHeader?: string) {
  const params = latestFeedParams(filters);
  params.set("limit", "24");
  params.delete("language");
  const settings = await getReaderFeedPreferences(signal, cookieHeader);
  for (const language of settings.languages) params.append("languages", language);
  if (!filters.content_type && settings.content_types.length < contentTypes.length)
    for (const type of settings.content_types) params.append("content_types", type);
  return read<FeedPage>(`/v1/feed?${params}`, undefined, signal);
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
  return read<FeedOptions>(`/v1/feed/options?${params}`, undefined, signal);
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
  return read<Topic[]>(`/v1/topics?${params}`, undefined, signal);
};
export const getTopic = (slug: string, signal?: AbortSignal) =>
  read<Topic>(`/v1/topics/${encodeURIComponent(slug)}`, undefined, signal);
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
  return read<Source[]>(`/v1/sources?${params}`, undefined, signal);
};
export const getSource = (id: string, signal?: AbortSignal) =>
  read<Source>(`/v1/sources/${encodeURIComponent(id)}`, undefined, signal);
export const getArticle = (slug: string, signal?: AbortSignal) =>
  read<Article>(`/v1/articles/${encodeURIComponent(slug)}`, undefined, signal);

export async function getTrending(cursor = "") {
  const origin = process.env.DEVFEED_USER_API_URL;
  if (!origin) throw new UserApiError(503);
  return read<FeedPage>(
    `/v1/user/trending?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    origin,
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
  return read<import("./search").SearchResponse>(
    `/v1/search?${params}`,
    undefined,
    signal ? AbortSignal.any([signal, deadline]) : deadline,
  );
}

export const getTag = (slug: string) =>
  read<{ id: string; name: string; slug: string }>(`/v1/tags/${encodeURIComponent(slug)}`);
export const getTags = (offset = 0, limit = 60) =>
  read<{ id: string; name: string; slug: string }[]>(`/v1/tags?limit=${limit}&offset=${offset}`);

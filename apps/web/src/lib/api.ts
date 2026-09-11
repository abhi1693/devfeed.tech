import "server-only";
import type { Article, FeedPage, FeedOptions, Source, Topic } from "./types";
import { feedParams, type FeedFilters } from "./feed-query";

export class UserApiError extends Error {
  constructor(public status: number) {
    super("The user service is unavailable");
  }
}
async function read<T>(
  path: string,
  origin = process.env.DEVFEED_PUBLIC_API_URL ?? "http://127.0.0.1:8000",
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(new URL(path, origin), {
      cache: "no-store",
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(8000)]) : AbortSignal.timeout(8000),
      headers: { Accept: "application/json" },
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
export function getFeed(filters: FeedFilters, signal?: AbortSignal) {
  const params = feedParams(filters);
  params.set("limit", "24");
  return read<FeedPage>(`/v1/feed?${params}`, undefined, signal);
}
export function getFeedOptions(filters: FeedFilters) {
  return read<FeedOptions>(`/v1/feed/options?${feedParams({ ...filters, cursor: "" })}`);
}
export const getTopics = (offset = 0, limit = 60) =>
  read<Topic[]>(`/v1/topics?limit=${limit}&offset=${offset}&has_articles=true`);
export const getTopic = (slug: string) =>
  read<Topic>(`/v1/topics/${encodeURIComponent(slug)}`);
export const getSources = (offset = 0, limit = 500) =>
  read<Source[]>(`/v1/sources?limit=${limit}&offset=${offset}&enabled=true`);
export const getSource = (id: string) =>
  read<Source>(`/v1/sources/${encodeURIComponent(id)}`);
export const getArticle = (id: string) =>
  read<Article>(`/v1/articles/${encodeURIComponent(id)}`);

export async function getTrending() {
  const origin = process.env.DEVFEED_USER_API_URL;
  if (!origin) throw new UserApiError(503);
  return read<FeedPage>("/v1/user/trending?limit=24", origin);
}

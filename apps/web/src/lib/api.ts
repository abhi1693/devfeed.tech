import "server-only";
import { cookies } from "next/headers";
import { userCookies } from "./server/gateway";
import { userApiOrigin } from "./server/config";
import type { Article, FeedPage, FeedOptions, Source, Topic } from "./types";
import { contentTypes, feedParams, type FeedFilters } from "./feed-query";

export class UserApiError extends Error {
  constructor(public status: number) {
    super("The user service is unavailable");
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
      headers: { Accept: "application/json", ...(cookie ? { Cookie: cookie } : {}) },
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
export async function getFeed(filters: FeedFilters, signal?: AbortSignal, cookieHeader?: string) {
  const params = feedParams(filters);
  params.set("limit", "24");
  if (!filters.content_type) {
    const cookie = userCookies(cookieHeader ?? (await cookies()).toString());
    if (/(?:^|; )(?:__Host-)?devfeed_user_session=/.test(cookie)) {
      try {
        const settings = await read<{ content_types: string[] }>(
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
        if (settings.content_types.length < contentTypes.length)
          for (const type of contentTypes.filter((type) => settings.content_types.includes(type)))
            params.append("content_types", type);
      } catch (error) {
        // Expired sessions browse anonymously; a service outage must not ignore preferences.
        if (!(error instanceof UserApiError && error.status === 401)) throw error;
      }
    }
  }
  return read<FeedPage>(`/v1/feed?${params}`, undefined, signal);
}
export function getFeedOptions(filters: FeedFilters) {
  return read<FeedOptions>(`/v1/feed/options?${feedParams({ ...filters, cursor: "" })}`);
}
export const getTopics = (offset = 0, limit = 60, signal?: AbortSignal) =>
  read<Topic[]>(`/v1/topics?limit=${limit}&offset=${offset}&has_articles=true`, undefined, signal);
export const getTopic = (slug: string) => read<Topic>(`/v1/topics/${encodeURIComponent(slug)}`);
export const getSources = (offset = 0, limit = 500, signal?: AbortSignal) =>
  read<Source[]>(
    `/v1/sources?limit=${limit}&offset=${offset}&enabled=true&has_articles=true`,
    undefined,
    signal,
  );
export const getSource = (id: string) => read<Source>(`/v1/sources/${encodeURIComponent(id)}`);
export const getArticle = (slug: string) =>
  read<Article>(`/v1/articles/${encodeURIComponent(slug)}`);

export async function getTrending(cursor = "") {
  const origin = process.env.DEVFEED_USER_API_URL;
  if (!origin) throw new UserApiError(503);
  return read<FeedPage>(
    `/v1/user/trending?limit=24${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    origin,
  );
}

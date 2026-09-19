// Shared allowlist for the extension and its server relay. Never accept raw URLs,
// search text, account identifiers, profile fields, or arbitrary GA parameters.
const identifier = (value: unknown) =>
  typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value);
const count = (value: unknown) =>
  Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 100;
const dimension = (value: unknown, max = 64) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= max &&
  !/[\x00-\x1f\x7f]/.test(value);
const measurement = (value: unknown) =>
  Number.isInteger(value) && Number(value) >= 0 && Number(value) <= 10000;
const choice =
  (...values: string[]) =>
  (value: unknown) =>
    typeof value === "string" && values.includes(value);
type Check = (value: unknown) => boolean;
const screens = [
  "/",
  "/articles",
  "/search",
  "/topics",
  "/topics/detail",
  "/sources",
  "/sources/detail",
  "/sources/suggest",
  "/latest",
  "/read-later",
  "/settings/profile",
  "/settings/appearance",
  "/settings/feed",
  "/settings/notifications",
  "/settings/topics",
  "/settings/sources",
  "/news",
  "/tutorials",
  "/releases",
  "/comparisons",
  "/opinions",
  "/other",
];
const fields: Record<string, Record<string, Check>> = {
  page_view: { page_path: choice(...screens) },
  user_engagement: { page_path: choice(...screens) },
  article_open: { article_id: identifier },
  article_like: { article_id: identifier },
  article_unlike: { article_id: identifier },
  article_bookmark: { article_id: identifier },
  article_unbookmark: { article_id: identifier },
  topic_follow: { topic_id: identifier },
  topic_unfollow: { topic_id: identifier },
  source_follow: { source_id: identifier },
  source_unfollow: { source_id: identifier },
  topics_saved: { selected_count: count },
  sources_saved: { selected_count: count },
  source_suggested: { source_type: choice("publisher", "aggregator") },
  feed_settings_saved: { feed_view: choice("cards", "compact"), selected_count: count },
  appearance_settings_saved: { theme: choice("light", "dark", "system") },
};
export type ExtensionEvent = { name: string; params: Record<string, string | number> };
export function extensionEvent(value: unknown): ExtensionEvent | null {
  if (!value || typeof value !== "object") return null;
  const { name, params } = value as { name?: unknown; params?: unknown };
  if (
    typeof name !== "string" ||
    !Object.hasOwn(fields, name) ||
    !params ||
    typeof params !== "object" ||
    Array.isArray(params)
  )
    return null;
  const schema = fields[name];
  const supplied = params as Record<string, unknown>;
  if (
    Object.keys(supplied).length !== Object.keys(schema).length ||
    !Object.entries(schema).every(([key, check]) => check(supplied[key]))
  )
    return null;
  return {
    name,
    params: Object.fromEntries(
      Object.keys(schema).map((key) => [key, supplied[key] as string | number]),
    ),
  };
}
export function extensionPage(route: string) {
  const path = route.split(/[?#]/)[0];
  if (path === "/settings") return "/settings/profile";
  if (screens.includes(path)) return path;
  if (/^\/articles\/[a-z0-9-]+$/i.test(path)) return "/articles";
  if (/^\/topics\/[a-z0-9-]+(?:\/[a-z-]+)?$/i.test(path)) return "/topics/detail";
  if (/^\/sources\/[a-z0-9-]+(?:\/[a-z-]+)?$/i.test(path)) return "/sources/detail";
  return "/other";
}
export function extensionPayload(value: unknown) {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  const event = extensionEvent(data.event);
  if (
    !event ||
    (data.client_platform !== undefined &&
      data.client_platform !== "chrome_extension" &&
      data.client_platform !== "edge_extension") ||
    typeof data.client_id !== "string" ||
    !/^\d{1,10}\.\d{1,10}$/.test(data.client_id) ||
    !Number.isSafeInteger(data.session_id) ||
    Number(data.session_id) <= 0 ||
    !Number.isInteger(data.engagement_time_msec) ||
    Number(data.engagement_time_msec) < 0 ||
    Number(data.engagement_time_msec) > 1_800_000 ||
    typeof data.extension_version !== "string" ||
    !/^\d{1,5}(?:\.\d{1,5}){1,3}$/.test(data.extension_version) ||
    (data.extension_surface !== undefined && data.extension_surface !== "newtab") ||
    (data.locale !== undefined && !dimension(data.locale, 35)) ||
    (data.timezone !== undefined && !dimension(data.timezone, 64)) ||
    (data.viewport_width !== undefined && !measurement(data.viewport_width)) ||
    (data.viewport_height !== undefined && !measurement(data.viewport_height))
  )
    return null;
  return {
    client_id: data.client_id as string,
    consent: { ad_user_data: "DENIED", ad_personalization: "DENIED" },
    events: [
      {
        name: event.name,
        params: {
          ...event.params,
          ...("page_path" in event.params
            ? {
                page_location: `https://extension.devfeed.tech${event.params.page_path}`,
                page_title: `DevFeed Extension ${event.params.page_path}`,
              }
            : {}),
          session_id: data.session_id as number,
          engagement_time_msec: data.engagement_time_msec as number,
          client_platform: data.client_platform ?? "chrome_extension",
          extension_version: data.extension_version,
          extension_surface: data.extension_surface ?? "newtab",
          page_category:
            "page_path" in event.params ? extensionPage(event.params.page_path as string) : "other",
          locale: data.locale ?? "und",
          timezone: data.timezone ?? "UTC",
          viewport_width: data.viewport_width ?? 0,
          viewport_height: data.viewport_height ?? 0,
        },
      },
    ],
  };
}

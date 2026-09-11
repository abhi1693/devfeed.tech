// Only these app events and explicit, non-personal parameters enter the GA queue.
export type AnalyticsEvents = {
  article_open: { article_id: string };
  article_like: { article_id: string };
  article_unlike: { article_id: string };
  topic_follow: { topic_id: string };
  topic_unfollow: { topic_id: string };
  source_follow: { source_id: string };
  source_unfollow: { source_id: string };
  topics_saved: { selected_count: number };
  sources_saved: { selected_count: number };
  source_suggested: { source_type: "publisher" | "aggregator" };
  feed_settings_saved: { feed_view: "cards" | "compact"; selected_count: number };
  appearance_settings_saved: { theme: "light" | "dark" | "system" };
};
export type AnalyticsEvent = { [K in keyof AnalyticsEvents]: { name: K; params: AnalyticsEvents[K] } }[keyof AnalyticsEvents];
export const analyticsEventName = "devfeed:analytics";

export function trackEvent<K extends keyof AnalyticsEvents>(name: K, params: AnalyticsEvents[K]) {
  if (typeof window === "undefined") return;
  // With no production analytics component mounted, there is no listener or queue.
  try { window.dispatchEvent(new CustomEvent(analyticsEventName, { detail: { name, params } })); } catch { /* Analytics must never break the action. */ }
}

export function trackUserMutation(path: string, method: string | undefined, result: unknown, body?: BodyInit | null) {
  if (method !== "PUT" && method !== "POST") return;
  if (!result || typeof result !== "object") return;
  const value = result as Record<string, unknown>;
  const like = /^articles\/([a-f0-9-]{36})\/like$/.exec(path);
  const follow = /^preferences\/(topics|sources)\/([a-f0-9-]{36})$/.exec(path);
  if (method === "PUT" && like && typeof value.liked === "boolean") {
    trackEvent(value.liked ? "article_like" : "article_unlike", { article_id: like[1] });
  } else if (method === "PUT" && follow && typeof value.followed === "boolean") {
    if (follow[1] === "topics") trackEvent(value.followed ? "topic_follow" : "topic_unfollow", { topic_id: follow[2] });
    else trackEvent(value.followed ? "source_follow" : "source_unfollow", { source_id: follow[2] });
  } else if (method === "PUT" && path === "preferences" && Array.isArray(value.topic_ids)) {
    trackEvent("topics_saved", { selected_count: value.topic_ids.length });
  } else if (method === "PUT" && path === "preferences/sources" && Array.isArray(value.source_ids)) {
    trackEvent("sources_saved", { selected_count: value.source_ids.length });
  } else if (method === "PUT" && path === "settings/feed" && (value.view === "cards" || value.view === "compact") && Array.isArray(value.content_types)) {
    trackEvent("feed_settings_saved", { feed_view: value.view, selected_count: value.content_types.length });
  } else if (method === "PUT" && path === "settings/appearance" && ["light", "dark", "system"].includes(String(value.theme))) {
    trackEvent("appearance_settings_saved", { theme: value.theme as "light" | "dark" | "system" });
  } else if (method === "POST" && path === "sources/suggestions" && typeof body === "string") {
    try {
      const { source_type: sourceType } = JSON.parse(body);
      if (sourceType === "publisher" || sourceType === "aggregator") trackEvent("source_suggested", { source_type: sourceType });
    } catch { /* Never send raw request bodies or user text. */ }
  }
}

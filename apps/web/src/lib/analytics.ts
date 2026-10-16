// Only these app events and explicit, non-personal parameters enter the GA queue.
export type AnalyticsEvents = {
  dev_card_view: Record<string, never>;
  dev_card_create_click: Record<string, never>;
  dev_card_preview_started: Record<string, never>;
  dev_card_signup_started: Record<string, never>;
  dev_card_saved: Record<string, never>;
  dev_card_share: { method: "link" | "markdown" | "download" | "download_x_header" };
  article_open: { article_id: string };
  article_like: { article_id: string };
  article_unlike: { article_id: string };
  article_bookmark: { article_id: string };
  article_unbookmark: { article_id: string };
  topic_follow: { topic_id: string };
  topic_unfollow: { topic_id: string };
  source_follow: { source_id: string };
  source_unfollow: { source_id: string };
  topics_saved: { selected_count: number };
  sources_saved: { selected_count: number };
  source_suggested: { source_type: "publisher" | "aggregator" };
  feed_settings_saved: { feed_view: "cards" | "compact"; selected_count: number };
  appearance_settings_saved: { theme: "light" | "dark" | "system" };
  search_query: {
    query_length: number;
    word_count: number;
    section: "all" | "articles" | "topics" | "sources" | "tags";
    result_count: number;
  };
  search_zero_result: {
    query_length: number;
    word_count: number;
    section: "all" | "articles" | "topics" | "sources" | "tags";
  };
  search_impression: {
    result_kind: "articles" | "topics" | "sources" | "tags";
    result_id: string;
    position: number;
  };
  search_click: {
    result_kind: "articles" | "topics" | "sources" | "tags";
    result_id: string;
    position: number;
  };
  search_conversion: {
    result_kind: "articles" | "topics" | "sources" | "tags";
    result_id: string;
    position: number;
  };
};
export type AnalyticsEvent = {
  [K in keyof AnalyticsEvents]: { name: K; params: AnalyticsEvents[K] };
}[keyof AnalyticsEvents];
export const analyticsEventName = "devfeed:analytics";

export function trackEvent<K extends keyof AnalyticsEvents>(name: K, params: AnalyticsEvents[K]) {
  if (typeof window === "undefined") return;
  // With no production analytics component mounted, there is no listener or queue.
  try {
    window.dispatchEvent(new CustomEvent(analyticsEventName, { detail: { name, params } }));
  } catch {
    /* Analytics must never break the action. */
  }
}

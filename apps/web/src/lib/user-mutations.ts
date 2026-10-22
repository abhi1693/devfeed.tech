import { ssoLogoutUrl } from "@devfeed/http/sso";
import { trackEvent, type AnalyticsEvent } from "./analytics";
import {
  validateReaderColumns,
  validateReaderPreferences,
  type ReaderColumn,
} from "./reader-preferences";
import { parseFeedViewScope, type FeedViewScope } from "./feed-view-scope";
import type { FeedViewPreference } from "./reader-layouts";
import {
  userRequest,
  type Appearance,
  type FeedDisplay,
  type NotificationDisplay,
  type UserProfile,
  type UserStack,
} from "./user";

export type MutationOptions = {
  csrfToken: string;
  signal?: AbortSignal;
  keepalive?: boolean;
};
type OpenOptions = Omit<MutationOptions, "csrfToken"> & { csrfToken?: string };
type MutationContract<Result> = {
  method?: "POST" | "PUT" | "DELETE";
  validate?: (result: unknown) => boolean;
  interestsChanged?: boolean;
  analytics?: (result: Result) => AnalyticsEvent;
};

export type Engagement = {
  article_id: string;
  likes: number;
  opens: number;
  liked: boolean;
  bookmarked?: boolean;
};
export type BookmarkResult = { article_id: string; bookmarked: boolean };
export type FollowResult = { followed: boolean };
export type SourceSuggestion = {
  feed_url: string;
  source_type: "publisher" | "aggregator";
  name?: string;
};
export type SuggestionReceipt = {
  id: string;
  name: string;
  approval_status: "pending";
  created_at: string;
};
export type ProfileSettingsInput = Partial<
  Omit<UserProfile, "avatar_variants" | "reading_streak" | "stack">
> & { stack?: Pick<UserStack, "topic_id" | "section" | "since_year">[] };

export const interestsChanged = "devfeed:interests-changed";

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
function engagement(value: unknown): boolean {
  return (
    record(value) &&
    typeof value.article_id === "string" &&
    typeof value.liked === "boolean" &&
    typeof value.likes === "number" &&
    Number.isFinite(value.likes) &&
    value.likes >= 0 &&
    typeof value.opens === "number" &&
    Number.isFinite(value.opens) &&
    value.opens >= 0 &&
    (value.bookmarked === undefined || typeof value.bookmarked === "boolean")
  );
}

/** A named mutation owns its acknowledgement and effects; transport never infers them. */
async function mutate<Result>(
  path: string,
  payload: unknown,
  options: OpenOptions,
  contract: MutationContract<Result> = {},
): Promise<Result> {
  options.signal?.throwIfAborted();
  const multipart = payload instanceof FormData;
  const result = await userRequest<Result>(path, {
    method: contract.method ?? "PUT",
    headers: {
      ...(options.csrfToken && { "X-CSRF-Token": options.csrfToken }),
      ...(payload !== undefined && !multipart && { "Content-Type": "application/json" }),
    },
    ...(payload !== undefined && { body: multipart ? payload : JSON.stringify(payload) }),
    ...(options.signal && { signal: options.signal }),
    ...(options.keepalive !== undefined && { keepalive: options.keepalive }),
  });
  options.signal?.throwIfAborted();
  if (contract.validate && !contract.validate(result))
    throw new Error("Invalid mutation acknowledgement");
  if (contract.interestsChanged && typeof window !== "undefined")
    window.dispatchEvent(new Event(interestsChanged));
  if (contract.analytics) {
    const event = contract.analytics(result);
    trackEvent(event.name, event.params);
  }
  return result;
}

export function setArticleLike(articleId: string, liked: boolean, options: MutationOptions) {
  return mutate<Engagement>(`articles/${encodeURIComponent(articleId)}/like`, { liked }, options, {
    validate: engagement,
    analytics: (result) => ({
      name: result.liked ? "article_like" : "article_unlike",
      params: { article_id: articleId },
    }),
  });
}

export function setArticleBookmark(
  articleId: string,
  bookmarked: boolean,
  options: MutationOptions,
) {
  return mutate<BookmarkResult>(
    `articles/${encodeURIComponent(articleId)}/bookmark`,
    { bookmarked },
    options,
    {
      validate: (value) =>
        record(value) &&
        typeof value.article_id === "string" &&
        typeof value.bookmarked === "boolean",
      analytics: (result) => ({
        name: result.bookmarked ? "article_bookmark" : "article_unbookmark",
        params: { article_id: articleId },
      }),
    },
  );
}

export function recordArticleOpen(articleId: string, options: OpenOptions = {}) {
  // Click analytics are emitted by the link itself, independently of this reading measurement.
  return mutate<Engagement>(`articles/${encodeURIComponent(articleId)}/open`, undefined, options, {
    method: "POST",
    validate: engagement,
  });
}

export function setTopicFollow(topicId: string, followed: boolean, options: MutationOptions) {
  return mutate<FollowResult>(
    `preferences/topics/${encodeURIComponent(topicId)}`,
    { followed },
    options,
    {
      validate: (value) => record(value) && typeof value.followed === "boolean",
      interestsChanged: true,
      analytics: (result) => ({
        name: result.followed ? "topic_follow" : "topic_unfollow",
        params: { topic_id: topicId },
      }),
    },
  );
}

export function setSourceFollow(sourceId: string, followed: boolean, options: MutationOptions) {
  return mutate<FollowResult>(
    `preferences/sources/${encodeURIComponent(sourceId)}`,
    { followed },
    options,
    {
      validate: (value) => record(value) && typeof value.followed === "boolean",
      interestsChanged: true,
      analytics: (result) => ({
        name: result.followed ? "source_follow" : "source_unfollow",
        params: { source_id: sourceId },
      }),
    },
  );
}

export function saveTopicPreferences(ids: string[], options: MutationOptions) {
  return mutate<{ topic_ids: string[] }>("preferences", { topic_ids: ids }, options, {
    validate: (value) => record(value) && strings(value.topic_ids),
    interestsChanged: true,
    analytics: (result) => ({
      name: "topics_saved",
      params: { selected_count: result.topic_ids.length },
    }),
  });
}

export function saveSourcePreferences(ids: string[], options: MutationOptions) {
  return mutate<{ source_ids: string[] }>("preferences/sources", { source_ids: ids }, options, {
    validate: (value) => record(value) && strings(value.source_ids),
    interestsChanged: true,
    analytics: (result) => ({
      name: "sources_saved",
      params: { selected_count: result.source_ids.length },
    }),
  });
}

export function saveFeedSettings(value: FeedDisplay, options: MutationOptions) {
  return mutate<FeedDisplay>("settings/feed", value, options, {
    validate: validateReaderPreferences,
    interestsChanged: true,
    analytics: (result) => ({
      name: "feed_settings_saved",
      params: { feed_view: result.view, selected_count: result.content_types.length },
    }),
  });
}

export type FeedViewReceipt = { view: FeedViewPreference };

export function validFeedViewReceipt(value: unknown): value is FeedViewReceipt {
  return (
    record(value) &&
    Object.keys(value).length === 1 &&
    typeof value.view === "string" &&
    ["cards", "compact", "stacked", "columns"].includes(value.view)
  );
}

export function saveFeedView(
  scope: FeedViewScope,
  view: FeedViewPreference,
  options: MutationOptions,
) {
  const key = parseFeedViewScope(scope);
  if (!key || !validFeedViewReceipt({ view }) || (view === "columns" && key !== "my_feed"))
    throw new TypeError("Unsupported feed layout");
  return mutate<FeedViewReceipt>(
    `settings/feed-view?feed=${encodeURIComponent(key)}`,
    { view },
    options,
    {
      validate: (value) =>
        validFeedViewReceipt(value) && (value.view !== "columns" || key === "my_feed"),
    },
  );
}

export type ReaderColumnsState = { initialized: boolean; columns: ReaderColumn[] };
export type StarterColumnsMode = "initialize" | "restore_empty";
export type ColumnWorkspaceScope = "latest" | "my_feed";

export function columnSettingsPath(workspace: ColumnWorkspaceScope = "latest") {
  return `settings/columns${workspace === "my_feed" ? "?workspace=my_feed" : ""}`;
}

export function validReaderColumnsState(value: unknown): value is ReaderColumnsState {
  return (
    record(value) && typeof value.initialized === "boolean" && validateReaderColumns(value.columns)
  );
}

/** Conditional installation cannot replace a workspace changed by another tab or device. */
export function initializeReaderColumns(
  columns: ReaderColumn[],
  mode: StarterColumnsMode,
  options: MutationOptions,
  workspace: ColumnWorkspaceScope = "latest",
) {
  return saveReaderColumns(columns, mode, options, workspace);
}

export function saveReaderColumns(
  columns: ReaderColumn[],
  mode: StarterColumnsMode | "replace",
  options: MutationOptions,
  workspace: ColumnWorkspaceScope = "latest",
) {
  return mutate<ReaderColumnsState>(columnSettingsPath(workspace), { columns, mode }, options, {
    validate: (value) => validReaderColumnsState(value) && value.initialized,
  });
}

export function saveAppearanceSettings(value: Partial<Appearance>, options: MutationOptions) {
  return mutate<Appearance>("settings/appearance", value, options, {
    validate: (result) =>
      record(result) &&
      (result.theme === "light" || result.theme === "dark" || result.theme === "system"),
    analytics: (result) => ({ name: "appearance_settings_saved", params: { theme: result.theme } }),
  });
}

export function previewSourceSuggestion(value: SourceSuggestion, options: MutationOptions) {
  return mutate<{ name: string }>("sources/suggestions/preview", value, options, {
    method: "POST",
    validate: (result) => record(result) && typeof result.name === "string",
  });
}

export function suggestSource(value: SourceSuggestion, options: MutationOptions) {
  const sourceType = value.source_type;
  return mutate<SuggestionReceipt>("sources/suggestions", value, options, {
    method: "POST",
    validate: (result) =>
      record(result) &&
      typeof result.id === "string" &&
      typeof result.name === "string" &&
      result.approval_status === "pending" &&
      typeof result.created_at === "string",
    analytics: () => ({ name: "source_suggested", params: { source_type: sourceType } }),
  });
}

export function saveProfileSettings(value: ProfileSettingsInput, options: MutationOptions) {
  return mutate<UserProfile>("settings/profile", value, options);
}

export function uploadAvatar(file: File, options: MutationOptions) {
  const data = new FormData();
  data.append("file", file);
  return mutate<UserProfile>("settings/profile/avatar", data, options, { method: "POST" });
}

export function removeAvatar(options: MutationOptions) {
  return mutate<UserProfile>("settings/profile/avatar", undefined, options, { method: "DELETE" });
}

export function saveNotificationSettings(value: NotificationDisplay, options: MutationOptions) {
  return mutate<NotificationDisplay>("settings/notifications", value, options, {
    validate: (result) =>
      record(result) && typeof result.show_badge === "boolean" && typeof result.sound === "boolean",
  });
}

export function claimMustReadsPresentation(
  value: { date: string; timezone: string; automatic: boolean },
  options: MutationOptions,
) {
  return mutate<{ claimed: boolean }>("must-reads/presentation", value, options, {
    method: "POST",
    validate: (result) => record(result) && typeof result.claimed === "boolean",
  });
}

export function revokeMcpConnection(connectionId: string, options: MutationOptions) {
  return mutate<void>(`mcp/connections/${encodeURIComponent(connectionId)}`, undefined, options, {
    method: "DELETE",
  });
}

export function respondToMcpConsent(
  requestId: string,
  value: { approved: boolean; access?: "read-only" | "read-write" },
  options: MutationOptions,
) {
  return mutate<{ redirect_url: string }>(
    `mcp/requests/${encodeURIComponent(requestId)}`,
    value,
    options,
    {
      method: "POST",
      validate: (result) => record(result) && typeof result.redirect_url === "string",
    },
  );
}

export async function logOut(options: MutationOptions) {
  const result = await mutate<unknown>("auth/sso/logout", undefined, options, { method: "POST" });
  return ssoLogoutUrl(result);
}

export function setTagFollow(tagId: string, followed: boolean, options: MutationOptions) {
  return mutate<FollowResult>(
    `preferences/tags/${encodeURIComponent(tagId)}`,
    { followed },
    options,
    {
      validate: (value) => record(value) && typeof value.followed === "boolean",
      interestsChanged: true,
    },
  );
}
export function saveTagPreferences(ids: string[], options: MutationOptions) {
  return mutate<{ tag_ids: string[] }>("preferences/tags", { tag_ids: ids }, options, {
    validate: (value) => record(value) && strings(value.tag_ids),
    interestsChanged: true,
  });
}

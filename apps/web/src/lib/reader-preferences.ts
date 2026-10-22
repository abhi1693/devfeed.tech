import { contentTypes } from "./feed-query";
import { languageOptions } from "./languages";
import { normalizeFeedView } from "./reader-layouts";
import type { FeedDisplay } from "./user";

export const maxReaderColumns = 12;
export const readerPreferencesVersion = 1;
export const guestReaderPreferencesKey = "devfeed:reader-preferences:guest";
export const legacyGuestFeedViewKey = "devfeed:feed-view:guest";

export type PersistedReaderView = "cards" | "compact" | "stacked" | "columns";
export type ReaderColumnFeed =
  { kind: "latest" | "recommended" } | { kind: "source" | "topic" | "tag"; id: string };
export type ReaderColumn = { id: string; feed: ReaderColumnFeed };
export type ReaderPreferences = FeedDisplay & { columns: ReaderColumn[] };
type GuestStorage = Pick<Storage, "getItem" | "setItem">;
export type GuestReaderPreferences = { value: ReaderPreferences; unavailable: boolean };

const views: readonly PersistedReaderView[] = ["cards", "compact", "stacked", "columns"];
const languages = new Set<string>(languageOptions.map(([code]) => code));

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]) {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every((key) => keys.includes(key));
}

function isView(value: unknown): value is PersistedReaderView {
  return typeof value === "string" && views.includes(value as PersistedReaderView);
}

/** Accept the API's UUID text forms and retain one canonical identity for comparisons. */
function canonicalUuid(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const identifier = value.startsWith("urn:uuid:")
    ? value.slice(9)
    : value.startsWith("{") && value.endsWith("}")
      ? value.slice(1, -1)
      : value;
  const hyphenated = /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(identifier);
  if (!hyphenated && (identifier !== value || !/^[\da-f]{32}$/i.test(identifier))) return null;
  const hex = identifier.replaceAll("-", "").toLowerCase();
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Compare selections independently of their column identity or accepted UUID text form. */
export function readerColumnFeedKey(feed: ReaderColumnFeed): string {
  return "id" in feed ? `${feed.kind}:${canonicalUuid(feed.id) ?? feed.id}` : feed.kind;
}

function column(value: unknown): ReaderColumn | null {
  if (!record(value) || !exactKeys(value, ["id", "feed"]) || !record(value.feed)) return null;
  const id = canonicalUuid(value.id);
  if (!id) return null;
  const feed = value.feed;
  if (feed.kind === "latest" || feed.kind === "recommended")
    return exactKeys(feed, ["kind"]) ? { id, feed: { kind: feed.kind } } : null;
  if (feed.kind === "source" || feed.kind === "topic" || feed.kind === "tag") {
    const target = canonicalUuid(feed.id);
    return target && exactKeys(feed, ["kind", "id"])
      ? { id, feed: { kind: feed.kind, id: target } }
      : null;
  }
  return null;
}

export function normalizeReaderColumns(value: unknown): ReaderColumn[] {
  if (!Array.isArray(value)) return [];
  const result: ReaderColumn[] = [];
  const seen = new Set<string>();
  const feeds = new Set<string>();
  for (const item of value) {
    const parsed = column(item);
    if (!parsed || seen.has(parsed.id) || feeds.has(readerColumnFeedKey(parsed.feed))) continue;
    seen.add(parsed.id);
    feeds.add(readerColumnFeedKey(parsed.feed));
    result.push(parsed);
    if (result.length === maxReaderColumns) break;
  }
  return result;
}

export function validateReaderColumns(value: unknown): value is ReaderColumn[] {
  if (!Array.isArray(value) || value.length > maxReaderColumns) return false;
  const seen = new Set<string>();
  const feeds = new Set<string>();
  return value.every((item) => {
    const parsed = column(item);
    if (!parsed || seen.has(parsed.id) || feeds.has(readerColumnFeedKey(parsed.feed))) return false;
    seen.add(parsed.id);
    feeds.add(readerColumnFeedKey(parsed.feed));
    return true;
  });
}

/** Stored settings may outlive a client release; recover usable data without changing storage. */
export function normalizeReaderPreferences(value: unknown): ReaderPreferences {
  const stored = record(value) ? value : {};
  const types = Array.isArray(stored.content_types)
    ? [...new Set(stored.content_types)].filter((item) => contentTypes.includes(item))
    : [];
  const selectedLanguages = Array.isArray(stored.languages)
    ? [...new Set(stored.languages)].filter((item): item is string => languages.has(item))
    : [];
  return {
    view: isView(stored.view) ? stored.view : "cards",
    columns: normalizeReaderColumns(stored.columns),
    content_types: types.length ? types : [...contentTypes],
    languages: selectedLanguages.length ? selectedLanguages : ["en"],
  };
}

/** Mutation acknowledgements must match the current schema; omitted columns support older APIs. */
export function validateReaderPreferences(value: unknown): value is FeedDisplay {
  return (
    record(value) &&
    Object.keys(value).every((key) =>
      ["view", "columns", "content_types", "languages"].includes(key),
    ) &&
    isView(value.view) &&
    Array.isArray(value.content_types) &&
    value.content_types.length > 0 &&
    value.content_types.length <= contentTypes.length &&
    value.content_types.every((item) => contentTypes.includes(item)) &&
    Array.isArray(value.languages) &&
    value.languages.length > 0 &&
    value.languages.length <= languages.size &&
    value.languages.every((item) => languages.has(item)) &&
    (value.columns === undefined || validateReaderColumns(value.columns))
  );
}

function browserStorage(): GuestStorage {
  return window.localStorage;
}

function storedRecord(storage: Pick<Storage, "getItem">): unknown {
  const raw = storage.getItem(guestReaderPreferencesKey);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
}

function unsupportedVersion(value: unknown) {
  return record(value) && "version" in value && value.version !== readerPreferencesVersion;
}

/** An explicit empty workspace is a user choice, including records predating the marker. */
export function guestColumnsInitialized(storage?: Pick<Storage, "getItem">): boolean {
  try {
    const stored = storedRecord(storage ?? browserStorage());
    if (!record(stored) || stored.version !== readerPreferencesVersion) return false;
    if (normalizeReaderColumns(stored.columns).length) return true;
    if (typeof stored.columns_initialized === "boolean") return stored.columns_initialized;
    return Array.isArray(stored.columns);
  } catch {
    return false;
  }
}

/** Guest data stays separate from account settings, including when a new account has defaults. */
export function readGuestReaderPreferences(
  storage?: Pick<Storage, "getItem">,
): GuestReaderPreferences {
  const fallback = { value: normalizeReaderPreferences(null), unavailable: false };
  try {
    const target = storage ?? browserStorage();
    const stored = storedRecord(target);
    if (unsupportedVersion(stored)) return { ...fallback, unavailable: true };
    if (record(stored) && stored.version === readerPreferencesVersion)
      return { value: normalizeReaderPreferences(stored), unavailable: false };
    return {
      value: normalizeReaderPreferences({
        view: normalizeFeedView(target.getItem(legacyGuestFeedViewKey)),
      }),
      unavailable: false,
    };
  } catch {
    // Storage denial must leave the current tab's layout controls usable.
    return fallback;
  }
}

/** Persist only guest-owned workspace state; callers never copy account settings here. */
export function writeGuestReaderPreferences(
  value: Pick<FeedDisplay, "view" | "columns">,
  storage?: GuestStorage,
  initialized = true,
): boolean {
  if (!isView(value.view) || !validateReaderColumns(value.columns ?? [])) return false;
  try {
    const target = storage ?? browserStorage();
    if (unsupportedVersion(storedRecord(target))) return false;
    const preferences = normalizeReaderPreferences(value);
    target.setItem(
      guestReaderPreferencesKey,
      JSON.stringify({
        version: readerPreferencesVersion,
        view: preferences.view,
        columns: preferences.columns,
        columns_initialized:
          initialized || !!preferences.columns.length || guestColumnsInitialized(target),
      }),
    );
    target.setItem(legacyGuestFeedViewKey, normalizeFeedView(preferences.view));
    return true;
  } catch {
    return false;
  }
}

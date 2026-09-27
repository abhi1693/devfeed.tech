import type { Article, Topic } from "../../web/src/lib/types";

const key = "devfeed:public-reader-cache";
const lifetime = 24 * 60 * 60 * 1000;
// Preserve the existing storage budget, measured in serialized UTF-16 characters.
const maxLength = 3_000_000;
type Entry<T> = { value: T; json?: string };
const articles = new Map<string, Entry<Article>>();
const topics = new Map<string, Entry<Topic>>();
let savedAt = Date.now();
let recordLength = 0;
let dirty = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let idle: number | undefined;

try {
  const raw = sessionStorage.getItem(key);
  if (raw && raw.length <= maxLength) {
    const value = JSON.parse(raw);
    if (
      typeof value?.at === "number" &&
      value.at <= Date.now() &&
      Date.now() - value.at < lifetime
    ) {
      savedAt = value.at;
      for (const article of (Array.isArray(value.articles) ? value.articles : []).slice(-48)) {
        if (
          typeof article?.slug === "string" &&
          typeof article.title === "string" &&
          Array.isArray(article.topics) &&
          Array.isArray(article.sources) &&
          Array.isArray(article.tags)
        )
          articles.set(article.slug, { value: article });
      }
      for (const topic of (Array.isArray(value.topics) ? value.topics : []).slice(-600)) {
        // Discard pre-managed-logo records so original URLs cannot survive an upgrade.
        if (
          typeof topic?.slug === "string" &&
          typeof topic.id === "string" &&
          Array.isArray(topic.logo_variants)
        )
          topics.set(topic.slug, { value: topic });
      }
      // One initialization pass; retained records are never serialized again.
      for (const entry of [...articles.values(), ...topics.values()]) {
        entry.json = JSON.stringify(entry.value);
        recordLength += entry.json.length;
      }
    }
  }
} catch {
  /* Browsing remains available if tab storage is unavailable. */
}

/** Public API records are immutable JSON values. Compare fresh responses without serializing them. */
function equal(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as Record<string, unknown>;
  const b = right as Record<string, unknown>;
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every((key) => Object.hasOwn(b, key) && equal(a[key], b[key]))
  );
}

function removeOldest<T>(records: Map<string, Entry<T>>) {
  const oldest = records.keys().next().value;
  if (oldest === undefined) return;
  recordLength -= records.get(oldest)?.json?.length ?? 0;
  records.delete(oldest);
}

function flush() {
  if (timer !== undefined) clearTimeout(timer);
  if (idle !== undefined) cancelIdleCallback(idle);
  timer = idle = undefined;
  if (!dirty) return;
  try {
    const prefix = `{"at":${savedAt},"articles":[`;
    const middle = '],"topics":[';
    const suffix = "]}";
    const envelopeLength = prefix.length + middle.length + suffix.length;
    for (const records of [articles, topics]) {
      for (const [slug, entry] of records) {
        if (entry.json === undefined) {
          entry.json = JSON.stringify(entry.value);
          recordLength += entry.json.length;
        }
        // An individually uncacheable response must not evict all useful records.
        if (entry.json.length + envelopeLength > maxLength) {
          recordLength -= entry.json.length;
          records.delete(slug);
        }
      }
    }
    const length = () =>
      prefix.length +
      middle.length +
      suffix.length +
      recordLength +
      Math.max(0, articles.size - 1) +
      Math.max(0, topics.size - 1);
    // Eviction only subtracts stored lengths; it never reserializes the cache.
    while (length() > maxLength && (articles.size || topics.size)) {
      if (topics.size) removeOldest(topics);
      else removeOldest(articles);
    }
    const json =
      prefix +
      [...articles.values()].map((entry) => entry.json).join(",") +
      middle +
      [...topics.values()].map((entry) => entry.json).join(",") +
      suffix;
    sessionStorage.setItem(key, json);
    dirty = false;
  } catch {
    /* Keep the in-memory copy and retry on the next update or page exit. */
  }
}

function schedule() {
  if (!dirty || timer !== undefined || idle !== undefined) return;
  // Bound the batching delay even while responses keep arriving. Give rendering
  // priority, with a deadline for pages that do not get an idle period.
  timer = setTimeout(() => {
    timer = undefined;
    if (typeof requestIdleCallback === "function")
      idle = requestIdleCallback(flush, { timeout: 1000 });
    else flush();
  }, 150);
}

function remember<T extends { slug: string }>(
  records: Map<string, Entry<T>>,
  items: T[],
  limit: number,
) {
  for (const item of items) {
    const previous = records.get(item.slug);
    if (previous && equal(previous.value, item)) continue;
    recordLength -= previous?.json?.length ?? 0;
    records.delete(item.slug);
    records.set(item.slug, { value: item });
    while (records.size > limit) removeOldest(records);
    savedAt = Date.now();
    dirty = true;
  }
  schedule();
}

// Only public article/topic records are cached, never sessions, recommendations,
// CSRF values, profiles, engagement state, or account preferences.
export const rememberArticles = (items: Article[]) => remember(articles, items, 48);
export const rememberTopics = (items: Topic[]) => remember(topics, items, 600);
export function cachedArticle(slug: string) {
  const entry = articles.get(slug);
  if (entry) {
    articles.delete(slug);
    articles.set(slug, entry);
  }
  return entry?.value;
}
export const cachedTopic = (slug: string) => topics.get(slug)?.value;

// A pending batch must survive a reload or a tab being backgrounded/closed.
window.addEventListener("pagehide", flush);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flush();
});

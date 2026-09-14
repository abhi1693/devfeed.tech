import type { Article, Topic } from "../../web/src/lib/types";

const key = "devfeed:public-reader-cache";
const lifetime = 24 * 60 * 60 * 1000;
const maxBytes = 3_000_000;
const articles = new Map<string, Article>();
const topics = new Map<string, Topic>();
let savedAt = Date.now();

try {
  const raw = sessionStorage.getItem(key);
  if (raw && raw.length <= maxBytes) {
    const value = JSON.parse(raw);
    if (
      typeof value.at === "number" &&
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
          articles.set(article.slug, article);
      }
      for (const topic of (Array.isArray(value.topics) ? value.topics : []).slice(-600)) {
        if (typeof topic?.slug === "string" && typeof topic.id === "string")
          topics.set(topic.slug, topic);
      }
    }
  }
} catch {
  /* Browsing remains available if tab storage is unavailable. */
}

function persist() {
  while (articles.size > 48) articles.delete(articles.keys().next().value!);
  while (topics.size > 600) topics.delete(topics.keys().next().value!);
  try {
    let json = JSON.stringify({
      at: savedAt,
      articles: [...articles.values()],
      topics: [...topics.values()],
    });
    while (json.length > maxBytes && (articles.size > 1 || topics.size > 0)) {
      if (topics.size) topics.delete(topics.keys().next().value!);
      else articles.delete(articles.keys().next().value!);
      json = JSON.stringify({
        at: savedAt,
        articles: [...articles.values()],
        topics: [...topics.values()],
      });
    }
    if (json.length <= maxBytes) sessionStorage.setItem(key, json);
  } catch {
    /* The in-memory copy still supports previews when storage is full. */
  }
}

// Only public article/topic records are cached, never sessions, recommendations,
// CSRF values, profiles, engagement state, or account preferences.
export function rememberArticles(items: Article[]) {
  savedAt = Date.now();
  for (const item of items) {
    articles.delete(item.slug);
    articles.set(item.slug, item);
  }
  persist();
}
export function rememberTopics(items: Topic[]) {
  for (const item of items) {
    topics.delete(item.slug);
    topics.set(item.slug, item);
  }
  persist();
}
export function cachedArticle(slug: string) {
  const article = articles.get(slug);
  if (article) {
    articles.delete(slug);
    articles.set(slug, article);
    persist();
  }
  return article;
}
export const cachedTopic = (slug: string) => topics.get(slug);

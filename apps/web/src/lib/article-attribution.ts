import type { Article } from "./types";

/** Prefer the verified publisher; a discovery source is still useful for navigation. */
export function articleSource(article: Pick<Article, "publisher" | "sources">) {
  if (article.publisher?.name.trim()) return article.publisher;
  return article.sources.find((source) => source.name.trim());
}

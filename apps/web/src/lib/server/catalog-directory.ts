import { cache } from "react";
import { notFound } from "next/navigation";
import { getSources, getTags, getTopics, UserApiError } from "../api";
import { CATALOG_PAGE_SIZE, catalogFilters, catalogPage, type CatalogPage } from "../catalog-page";
import type { SearchParams } from "../feed-query";
import { catalogCanonical, pageMetadata } from "../metadata";
import type { Source, Tag, Topic } from "../types";

type Items = { topics: Topic; sources: Source; tags: Tag };
type Kind = keyof Items;
const descriptions = {
  topics: ["Explore topics", "Explore developer topics and find the latest published articles."],
  sources: ["Sources", "Discover sources publishing developer news, tutorials, and releases."],
  tags: ["Explore tags", "Discover developer articles by tag."],
} as const;

/** Share one bounded existence decision between metadata and page, before either can stream. */
export const loadCatalogDirectory = cache(
  async <K extends Kind>(kind: K, offset: number, query: string, letter: string) => {
    let items: (Source | Topic | Tag)[];
    try {
      const limit = CATALOG_PAGE_SIZE + 1;
      items =
        kind === "topics"
          ? query
            ? await getTopics(offset, limit, undefined, "name", query)
            : await getTopics(offset, limit)
          : kind === "sources"
            ? query
              ? await getSources(offset, limit, undefined, query)
              : await getSources(offset, limit)
            : await getTags(offset, limit, query, letter);
    } catch (error) {
      if (error instanceof UserApiError && error.status >= 500) return null;
      throw error;
    }
    if (offset > 0 && items.length === 0) notFound();
    return catalogPage(items, offset) as CatalogPage<Items[K]>;
  },
);

export async function catalogDirectoryMetadata(kind: Kind, params: SearchParams) {
  const { offset, query, letter } = catalogFilters(params, kind);
  const page = await loadCatalogDirectory(kind, offset, query, letter);
  if (!page)
    return { title: "Page temporarily unavailable", robots: { index: false, follow: false } };
  const [title, description] = descriptions[kind];
  return {
    ...pageMetadata(title, description, catalogCanonical(`/${kind}`, params)),
    ...(query || letter || !page.items.length ? { robots: { index: false, follow: true } } : {}),
  };
}

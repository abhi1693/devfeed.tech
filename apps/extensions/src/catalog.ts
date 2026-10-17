import { readerRequest } from "../../web/src/lib/reader-runtime";
import type { CatalogPage } from "../../web/src/lib/catalog-page";
import type { Source, Topic, Tag } from "../../web/src/lib/types";

export async function catalog<T extends Source | Topic | Tag>(
  kind: "topics" | "sources" | "tags",
  signal: AbortSignal,
  offset = 0,
  query = "",
  letter = "",
): Promise<CatalogPage<T>> {
  const response = await readerRequest(
    `/api/v1/${kind}?${new URLSearchParams({ offset: String(offset), q: query, letter })}`,
    { signal },
  );
  if (!response.ok) throw new Error(`Couldn’t load ${kind}`);
  return (await response.json()) as CatalogPage<T>;
}
export async function catalogItem(
  kind: "topics" | "sources" | "tags",
  slug: string,
  signal: AbortSignal,
) {
  const response = await readerRequest(`/api/v1/${kind}/${encodeURIComponent(slug)}`, { signal });
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`Couldn’t load ${kind}`);
  return (await response.json()) as Source | Topic | Tag;
}

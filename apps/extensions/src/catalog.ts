import { readerRequest } from "../../web/src/lib/reader-runtime";
import type { Source, Topic } from "../../web/src/lib/types";

export async function catalog<T extends Source | Topic>(
  kind: "topics" | "sources",
  signal: AbortSignal,
  offset = 0,
): Promise<T[]> {
  const response = await readerRequest(`/api/v1/${kind}?offset=${offset}`, { signal });
  if (!response.ok) throw new Error(`Couldn’t load ${kind}`);
  return ((await response.json()) as { items: T[] }).items;
}
export async function catalogItem(kind: "topics" | "sources", slug: string, signal: AbortSignal) {
  const response = await readerRequest(`/api/v1/${kind}/${encodeURIComponent(slug)}`, { signal });
  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`Couldn’t load ${kind}`);
  return (await response.json()) as Source | Topic;
}

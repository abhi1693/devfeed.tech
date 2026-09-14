import { readerRequest } from "../../web/src/lib/reader-runtime";
import type { Source, Topic } from "../../web/src/lib/types";

export async function catalog<T extends Source | Topic>(
  kind: "topics" | "sources",
  signal: AbortSignal,
  offset = 0,
  all = false,
): Promise<T[]> {
  const items: T[] = [];
  const visited = new Set<string>();
  let cursor: string | null = String(offset);
  while (cursor !== null && !visited.has(cursor)) {
    visited.add(cursor);
    const response = await readerRequest(`/api/v1/${kind}?offset=${encodeURIComponent(cursor)}`, {
      signal,
    });
    if (!response.ok) throw new Error(`Couldn’t load ${kind}`);
    const page = (await response.json()) as { items: T[]; next_cursor: string | null };
    items.push(...page.items);
    if (!all) break;
    cursor = page.next_cursor;
  }
  return items;
}

export async function catalogItem(kind: "topics" | "sources", slug: string, signal: AbortSignal) {
  const bounded = AbortSignal.any([signal, AbortSignal.timeout(15000)]);
  for (let offset = 0; offset <= 1_000_000; offset += 60) {
    const items = await catalog(kind, bounded, offset);
    const item = items.find((value) => value.slug === slug || value.id === slug);
    if (item) return item;
    if (items.length < 60) return undefined;
  }
}

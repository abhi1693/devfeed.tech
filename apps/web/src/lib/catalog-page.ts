export const CATALOG_PAGE_SIZE = 60;
export const MAX_CATALOG_OFFSET = 1_000_000;
export type CatalogPage<T> = { items: T[]; next_cursor: string | null };
export function catalogOffset(value: unknown) {
  return typeof value === "string" && /^\d+$/.test(value)
    ? Math.min(Number(value), MAX_CATALOG_OFFSET)
    : 0;
}
export function catalogPage<T>(items: T[], offset: number): CatalogPage<T> {
  return {
    items,
    next_cursor:
      items.length === CATALOG_PAGE_SIZE && offset + items.length <= MAX_CATALOG_OFFSET
        ? String(offset + items.length)
        : null,
  };
}

export const CATALOG_PAGE_SIZE = 60;
export const MAX_CATALOG_OFFSET = 1_000_000;
export type CatalogPage<T> = { items: T[]; next_cursor: string | null };
export type CatalogFilters = { offset: number; query: string; letter: string };
export function catalogFilters(
  params: Record<string, unknown> | URLSearchParams,
  kind: "topics" | "sources" | "tags",
): CatalogFilters {
  const value = (key: string) => {
    if (!(params instanceof URLSearchParams)) return params[key];
    const values = params.getAll(key);
    return values.length === 1 ? values[0] : undefined;
  };
  const query = value("q");
  const letter = value("letter");
  return {
    offset: catalogOffset(value("offset")),
    query: typeof query === "string" ? query.trim().slice(0, 200) : "",
    letter: kind === "tags" && typeof letter === "string" && /^[A-Z#]$/.test(letter) ? letter : "",
  };
}
export function catalogHref(
  kind: "topics" | "sources" | "tags",
  {
    query = "",
    letter = "",
    offset = 0,
  }: Partial<Omit<CatalogFilters, "offset">> & { offset?: number | string } = {},
) {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (kind === "tags" && letter) params.set("letter", letter);
  if (offset && offset !== "0") params.set("offset", String(offset));
  return `/${kind}${params.size ? `?${params}` : ""}`;
}
export function catalogOffset(value: unknown) {
  return typeof value === "string" && /^\d+$/.test(value)
    ? Math.min(Number(value), MAX_CATALOG_OFFSET)
    : 0;
}
export function catalogPage<T>(items: T[], offset: number): CatalogPage<T> {
  return {
    items: items.slice(0, CATALOG_PAGE_SIZE),
    next_cursor:
      items.length > CATALOG_PAGE_SIZE && offset + CATALOG_PAGE_SIZE <= MAX_CATALOG_OFFSET
        ? String(offset + CATALOG_PAGE_SIZE)
        : null,
  };
}

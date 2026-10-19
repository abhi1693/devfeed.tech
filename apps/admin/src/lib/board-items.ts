/** Running work first, then oldest evidence; retain database submillisecond ordering. */
export function compareBoardItems(
  a: { state: string; timestamp: string; key: string },
  b: { state: string; timestamp: string; key: string },
) {
  const running = Number(b.state === "running") - Number(a.state === "running");
  if (running) return running;
  const elapsed = Date.parse(a.timestamp) - Date.parse(b.timestamp);
  if (elapsed) return elapsed;
  const fraction = (timestamp: string) => (timestamp.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0");
  return (
    fraction(a.timestamp).localeCompare(fraction(b.timestamp)) ||
    (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
  );
}

/** Later lane pages replace older copies when an item moves between columns. */
export function boardItems<Item>(
  columns: readonly { items: readonly Item[] }[],
  extra: readonly ({ items: readonly Item[] } | undefined)[],
  key: (item: Item) => string,
) {
  const items = new Map<string, Item>();
  for (const column of [...columns, ...extra])
    for (const item of column?.items ?? []) items.set(key(item), item);
  return [...items.values()];
}

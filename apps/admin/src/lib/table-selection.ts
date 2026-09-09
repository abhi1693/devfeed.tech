/** Read the current filter across server pages before offering a bulk operation.
 * Keep full snapshots for review guards, and deduplicate rows if concurrent edits
 * move records between pages. Nothing is mutated during selection. */
export async function loadMatchingRows<T>(
  loadPage: (offset: number, limit: number, signal: AbortSignal) => Promise<{ items: T[]; total: number }>,
  getRowId: (row: T) => string,
  signal: AbortSignal,
): Promise<T[]> {
  const rows = new Map<string, T>();
  let offset = 0;
  while (true) {
    signal.throwIfAborted();
    const page = await loadPage(offset, 100, signal);
    signal.throwIfAborted();
    for (const row of page.items) rows.set(getRowId(row), row);
    offset += page.items.length;
    if (!page.items.length || offset >= page.total) return [...rows.values()];
  }
}

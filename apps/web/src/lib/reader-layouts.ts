/** Shared reader choices; storage values remain compatible with existing preferences. */
export const readerLayouts = [
  {
    id: "grid",
    label: "Grid",
    value: "cards",
    description: "Image previews with room to browse.",
    available: true,
  },
  {
    id: "list",
    label: "List",
    value: "compact",
    description: "Compact rows for scanning headlines.",
    available: true,
  },
  {
    id: "stacked",
    label: "Stacked",
    value: "stacked",
    description: "Headline, thumbnail, and a short description in each row.",
    available: true,
  },
  {
    id: "columns",
    label: "Columns",
    value: "columns",
    description: "Arrange your own workspace of saved feeds.",
    available: true,
  },
] as const;

export type ReaderLayout = (typeof readerLayouts)[number]["id"];
type AvailableReaderLayout = Extract<(typeof readerLayouts)[number], { available: true }>;
export const availableReaderLayouts = readerLayouts.filter(
  (layout): layout is AvailableReaderLayout => layout.available,
);
export type FeedViewPreference = AvailableReaderLayout["value"];

export function normalizeFeedView(value: unknown): FeedViewPreference {
  return availableReaderLayouts.find((layout) => layout.value === value)?.value ?? "cards";
}

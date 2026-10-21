import { expect, it } from "vitest";
import { availableReaderLayouts, normalizeFeedView, readerLayouts } from "@/lib/reader-layouts";

it("exposes all four implemented reader layouts", () => {
  expect(readerLayouts.map(({ id }) => id)).toEqual(["grid", "list", "stacked", "columns"]);
  expect(availableReaderLayouts.map(({ id, label, value }) => ({ id, label, value }))).toEqual([
    { id: "grid", label: "Grid", value: "cards" },
    { id: "list", label: "List", value: "compact" },
    { id: "stacked", label: "Stacked", value: "stacked" },
    { id: "columns", label: "Columns", value: "columns" },
  ]);
  expect(readerLayouts.every(({ available }) => available)).toBe(true);
});

it.each(["cards", "compact", "stacked", "columns"])(
  "preserves existing saved %s preferences",
  (view) => {
    expect(normalizeFeedView(view)).toBe(view);
  },
);

it.each([undefined, null, "", "grid", "list", "unknown", 1, {}, []])(
  "defaults unsupported saved values to the available Grid layout (%j)",
  (view) => {
    expect(normalizeFeedView(view)).toBe("cards");
  },
);

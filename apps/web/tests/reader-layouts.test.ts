import { expect, it } from "vitest";
import { availableReaderLayouts, normalizeFeedView, readerLayouts } from "@/lib/reader-layouts";

it("defines the four reader layouts while exposing only implemented choices", () => {
  expect(readerLayouts.map(({ id }) => id)).toEqual(["grid", "list", "stacked", "columns"]);
  expect(availableReaderLayouts.map(({ id, label, value }) => ({ id, label, value }))).toEqual([
    { id: "grid", label: "Grid", value: "cards" },
    { id: "list", label: "List", value: "compact" },
  ]);
  expect(readerLayouts.filter(({ available }) => !available).map(({ id }) => id)).toEqual([
    "stacked",
    "columns",
  ]);
});

it.each(["cards", "compact"])("preserves existing saved %s preferences", (view) => {
  expect(normalizeFeedView(view)).toBe(view);
});

it.each([undefined, null, "", "grid", "list", "stacked", "columns", "unknown", 1, {}, []])(
  "defaults unsupported saved values to the available Grid layout (%j)",
  (view) => {
    expect(normalizeFeedView(view)).toBe("cards");
  },
);

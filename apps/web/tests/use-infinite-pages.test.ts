import { expect, it } from "vitest";
import { filterUniquePageItems, flattenPageItems, uniquePageItems } from "@/lib/use-infinite-pages";

const pages = [
  {
    items: [
      { id: "first", value: 1 },
      { id: "shared", value: 1 },
    ],
    next_cursor: "next",
  },
  {
    items: [
      { id: "shared", value: 2 },
      { id: "last", value: 2 },
    ],
    next_cursor: null,
  },
];

it("flattens pages and makes duplicate retention explicit", () => {
  const items = flattenPageItems(pages);
  expect(uniquePageItems(items, (item) => item.id)).toEqual([
    { id: "first", value: 1 },
    { id: "shared", value: 1 },
    { id: "last", value: 2 },
  ]);
  expect(uniquePageItems(items, (item) => item.id, "last")).toEqual([
    { id: "first", value: 1 },
    { id: "shared", value: 2 },
    { id: "last", value: 2 },
  ]);
});

it("filters unique items without collapsing page boundaries", () => {
  expect(
    filterUniquePageItems(
      pages,
      (item) => item.id,
      (item) => item.id !== "first",
    ),
  ).toEqual([
    { items: [{ id: "shared", value: 1 }], next_cursor: "next" },
    { items: [{ id: "last", value: 2 }], next_cursor: null },
  ]);
});

import fc from "fast-check";
import { expect, it } from "vitest";
import {
  catalogOffset,
  catalogPage,
  CATALOG_PAGE_SIZE,
  MAX_CATALOG_OFFSET,
} from "@/lib/catalog-page";
import { filterUniquePageItems, flattenPageItems, uniquePageItems } from "@/lib/use-infinite-pages";
import { propertyOptions } from "../../../scripts/ci/property-config.mjs";

const item = fc.record({ id: fc.integer({ min: 0, max: 12 }).map(String), revision: fc.nat() });
const pages = fc.array(
  fc.record({ items: fc.array(item, { maxLength: 40 }), label: fc.string() }),
  {
    maxLength: 15,
  },
);

it("deduplicates repeated pages in stable first-seen order with the requested revision", () => {
  fc.assert(
    fc.property(pages, fc.constantFrom("first", "last"), (input, keep) => {
      const snapshot = structuredClone(input);
      const items = input.flatMap((page) => page.items);
      const ids = [...new Set(items.map((entry) => entry.id))];
      const expected = ids.map((id) =>
        keep === "first"
          ? items.find((entry) => entry.id === id)
          : items.findLast((entry) => entry.id === id),
      );
      const result = uniquePageItems(flattenPageItems(input), (entry) => entry.id, keep);
      expect(result).toEqual(expected);
      expect(uniquePageItems(result, (entry) => entry.id, keep)).toEqual(result);
      expect(input).toEqual(snapshot);
    }),
    propertyOptions(),
  );
});

it("filters before deduplication while preserving page metadata and input data", () => {
  fc.assert(
    fc.property(pages, fc.nat({ max: 10 }), (input, minimumRevision) => {
      const snapshot = structuredClone(input);
      const eligible = input
        .flatMap((page) => page.items)
        .filter((entry) => entry.revision >= minimumRevision);
      const expected = eligible.filter(
        (entry, index) => eligible.findIndex((other) => other.id === entry.id) === index,
      );
      const result = filterUniquePageItems(
        input,
        (entry) => entry.id,
        (entry) => entry.revision >= minimumRevision,
      );
      expect(flattenPageItems(result)).toEqual(expected);
      expect(result.map((page) => page.label)).toEqual(input.map((page) => page.label));
      expect(filterUniquePageItems(result, (entry) => entry.id)).toEqual(result);
      expect(input).toEqual(snapshot);
    }),
    propertyOptions(),
  );
});

it("advances catalog cursors without gaps or loops and stops at the offset bound", () => {
  fc.assert(
    fc.property(
      fc.array(fc.nat(), { maxLength: 300 }),
      fc.integer({ min: 0, max: MAX_CATALOG_OFFSET }),
      (items, start) => {
        const received: number[] = [];
        const cursors: number[] = [];
        let offset = start;
        do {
          cursors.push(offset);
          const page = catalogPage(
            items.slice(offset - start, offset - start + CATALOG_PAGE_SIZE),
            offset,
          );
          received.push(...page.items);
          if (page.next_cursor === null) break;
          const next = catalogOffset(page.next_cursor);
          expect(next).toBe(offset + CATALOG_PAGE_SIZE);
          expect(next).toBeLessThanOrEqual(MAX_CATALOG_OFFSET);
          expect(cursors).not.toContain(next);
          offset = next;
        } while (true);
        const capacity =
          (Math.floor((MAX_CATALOG_OFFSET - start) / CATALOG_PAGE_SIZE) + 1) * CATALOG_PAGE_SIZE;
        expect(received).toEqual(items.slice(0, capacity));
      },
    ),
    propertyOptions(),
  );
});

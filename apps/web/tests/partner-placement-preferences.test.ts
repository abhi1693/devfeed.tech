// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { recordPlacementView } from "@/lib/partner-placement-preferences";
import { placement } from "./partner-placement-fixtures";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-11T10:00:00Z"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  localStorage.clear();
});

it("records optional view history across repeated views and UTC days", () => {
  for (let index = 0; index < 50; index++) {
    expect(recordPlacementView(placement.product_id)).toBe(true);
  }
  expect(
    JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1")!).entries,
  ).toEqual([{ id: placement.product_id, day: "2026-10-11", count: 50, seen: Date.now() }]);
  vi.setSystemTime(new Date("2026-10-12T10:00:00Z"));
  expect(recordPlacementView(placement.product_id)).toBe(true);
  expect(
    JSON.parse(localStorage.getItem("devfeed:partner-placement-preferences:v1")!).entries,
  ).toEqual([{ id: placement.product_id, day: "2026-10-12", count: 1, seen: Date.now() }]);
});

it.each([0, 86_400_000, 91 * 86_400_000])(
  "drops legacy hides and cap metadata when recording history aged %s milliseconds",
  (age) => {
    localStorage.setItem(
      "devfeed:partner-placement-preferences:v1",
      JSON.stringify({
        version: 1,
        entries: [
          {
            id: placement.product_id,
            day: new Date(Date.now() - age).toISOString().slice(0, 10),
            count: 50,
            cap: 1,
            seen: Date.now() - age,
            hidden: Date.now() + 90 * 86_400_000,
          },
        ],
      }),
    );
    expect(recordPlacementView(placement.product_id)).toBe(true);
    const preferences = JSON.parse(
      localStorage.getItem("devfeed:partner-placement-preferences:v1")!,
    );
    expect(
      preferences.entries.every(
        (entry: Record<string, unknown>) => !("cap" in entry) && !("hidden" in entry),
      ),
    ).toBe(true);
  },
);

it("bounds optional view history to the 100 most recently viewed products", () => {
  for (let index = 0; index < 110; index++)
    recordPlacementView(`${index.toString(16).padStart(8, "0")}-1111-4111-8111-111111111111`);
  const entries = JSON.parse(
    localStorage.getItem("devfeed:partner-placement-preferences:v1")!,
  ).entries;
  expect(entries).toHaveLength(100);
  expect(entries[0].id).toBe("0000006d-1111-4111-8111-111111111111");
  expect(entries.at(-1).id).toBe("0000000a-1111-4111-8111-111111111111");
});

it("reports optional history failure without throwing on malformed or unwritable storage", () => {
  localStorage.setItem("devfeed:partner-placement-preferences:v1", "{bad");
  expect(recordPlacementView(placement.product_id)).toBe(false);
  localStorage.clear();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(recordPlacementView(placement.product_id)).toBe(false);
});

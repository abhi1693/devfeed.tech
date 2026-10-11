// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  hidePartnerProduct,
  placementExclusions,
  recordPlacementView,
} from "@/lib/partner-placement-preferences";
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

it("persists a daily product cap and a short repeat cooldown across visits", () => {
  expect(placementExclusions()).toEqual([]);
  recordPlacementView(placement.product_id, 2);
  expect(placementExclusions()).toEqual([placement.product_id]);
  vi.advanceTimersByTime(31 * 60_000);
  expect(placementExclusions()).toEqual([]);
  recordPlacementView(placement.product_id, 2);
  vi.advanceTimersByTime(31 * 60_000);
  expect(placementExclusions()).toEqual([placement.product_id]);
  vi.setSystemTime(new Date("2026-10-12T10:00:00Z"));
  expect(placementExclusions()).toEqual([]);
});

it("persists a hidden product for 90 days independently of campaign or account", () => {
  hidePartnerProduct(placement.product_id, 2);
  vi.advanceTimersByTime(89 * 86_400_000);
  expect(placementExclusions()).toEqual([placement.product_id]);
  vi.advanceTimersByTime(2 * 86_400_000);
  expect(placementExclusions()).toEqual([]);
});

it("bounds persisted history and exclusions to 100 products", () => {
  for (let index = 0; index < 110; index++)
    hidePartnerProduct(`${index.toString(16).padStart(8, "0")}-1111-4111-8111-111111111111`, 2);
  expect(placementExclusions()).toHaveLength(100);
});

it("fails closed for malformed, blocked or unwritable preference storage", () => {
  localStorage.setItem("devfeed:partner-placement-preferences:v1", "{bad");
  expect(placementExclusions()).toBeNull();
  localStorage.clear();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(placementExclusions()).toBeNull();
  expect(recordPlacementView(placement.product_id, 2)).toBe(false);
});

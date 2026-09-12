import { afterEach, expect, it, vi } from "vitest";
import { analyticsMeasurementId } from "@/lib/server/config";

afterEach(() => vi.unstubAllEnvs());

it("disables GA4 in production-mode Compose when its runtime flag is false", () => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", "false");
  vi.stubEnv("GOOGLE_ANALYTICS_ID", "G-LOCALTEST");
  expect(analyticsMeasurementId()).toBe("");
});

it("uses runtime changes without rebuilding and preserves existing production defaults", () => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", undefined);
  vi.stubEnv("GOOGLE_ANALYTICS_ID", undefined);
  expect(analyticsMeasurementId()).toBe("G-N4V5CW5C0M");
  vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", "true");
  vi.stubEnv("GOOGLE_ANALYTICS_ID", " G-OVERRIDE ");
  expect(analyticsMeasurementId()).toBe("G-OVERRIDE");
  vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", "false");
  expect(analyticsMeasurementId()).toBe("");
});

it("never enables GA4 in development or tests, even with the flag set", () => {
  vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", "true");
  for (const mode of ["development", "test"]) {
    vi.stubEnv("NODE_ENV", mode);
    expect(analyticsMeasurementId()).toBe("");
  }
});

it("fails closed for blank or unrecognized boolean values", () => {
  vi.stubEnv("NODE_ENV", "production");
  for (const value of ["", "0", "1", "flase", "false"]) {
    vi.stubEnv("DEVFEED_ANALYTICS_ENABLED", value);
    expect(analyticsMeasurementId()).toBe("");
  }
});

// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  acquisitionStorageKey,
  captureReaderAcquisition,
  clearReaderAcquisition,
  currentReaderMeasurement,
  prepareReaderLoginAcquisition,
  readerAcquisitionFragment,
  refreshReaderMeasurement,
  sanitizeReaderMeasurement,
  startReaderAcquisition,
} from "@/lib/reader-acquisition";

const cleanups: (() => void)[] = [];
const gaId = "G-READER";
const now = Math.floor(Date.now() / 1000);
const capture = (
  path = "/articles/private-slug?q=private@example.test#private",
  referrer = "https://www.google.com/search?q=private@example.test",
) =>
  captureReaderAcquisition({
    url: new URL(path, "https://devfeed.tech"),
    referrer,
    platform: "web",
    now,
  });
const eligible = () => ({
  ...capture(),
  measurement: "eligible",
  client_id: "123.456",
  session_id: 789,
});
const start = (options: Partial<Parameters<typeof startReaderAcquisition>[0]> = {}) => {
  cleanups.push(
    startReaderAcquisition({
      enabled: true,
      gaId,
      url: new URL("https://devfeed.tech/articles/first"),
      referrer: "https://www.google.com/search?q=private",
      ...options,
    }),
  );
};
const analytics = window as Window & {
  gtag?: (...args: unknown[]) => void;
  "ga-disable-G-READER"?: boolean;
};
function existingTag() {
  const script = document.createElement("script");
  script.dataset.devfeedGa = gaId;
  document.head.append(script);
}
afterEach(() => {
  cleanups
    .splice(0)
    .reverse()
    .forEach((cleanup) => cleanup());
  clearReaderAcquisition();
  sessionStorage.clear();
  localStorage.clear();
  document.querySelectorAll("script[data-devfeed-ga]").forEach((script) => script.remove());
  delete analytics.gtag;
  delete analytics["ga-disable-G-READER"];
  history.replaceState(null, "", "/");
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("retains only approved coarse acquisition values and removes raw paths, searches and referrers", () => {
  expect(capture()).toEqual({
    version: 1,
    status: "attributed",
    source: "google",
    medium: "organic",
    campaign: null,
    landing_route: "article",
    captured_at: now,
    client_platform: "web",
  });
  expect(JSON.stringify(capture())).not.toContain("private");
  expect(
    capture("/settings/profile?email=private", "https://private.example/secret"),
  ).toMatchObject({ status: "unknown", source: null, medium: "unknown", landing_route: "other" });
  expect(capture("/latest", "")).toMatchObject({
    status: "direct",
    source: "direct",
    medium: "none",
  });
});

it.each([
  "/login?return_to=/private",
  "/api/v1/user/auth/callback?code=private",
  "/extension/login-complete",
])("does not claim %s as an initial landing", (path) => {
  expect(capture(path)).toMatchObject({ status: "unavailable", landing_route: "unavailable" });
});

it("accepts configured campaigns only and never preserves arbitrary UTM strings", () => {
  const base = { referrer: "", platform: "web" as const, campaigns: ["organic-launch"], now };
  expect(
    captureReaderAcquisition({
      ...base,
      url: new URL(
        "https://devfeed.tech/?utm_source=google&utm_medium=organic&utm_campaign=organic-launch",
      ),
    }),
  ).toMatchObject({ status: "attributed", campaign: "organic-launch" });
  expect(
    captureReaderAcquisition({
      ...base,
      url: new URL(
        "https://devfeed.tech/?utm_source=google&utm_medium=organic&utm_campaign=private-person",
      ),
    }).campaign,
  ).toBeNull();
  expect(
    captureReaderAcquisition({
      ...base,
      url: new URL(
        "https://devfeed.tech/?utm_source=private@example.test&utm_medium=organic&utm_campaign=private-person",
      ),
    }),
  ).toMatchObject({ status: "unknown", source: null, campaign: null });
  expect(capture("/?utm_source=twitter&utm_medium=social", "")).toMatchObject({
    source: "x",
    medium: "social",
  });
});

it.each([
  { email: "private@example.test" },
  { landing_route: "/settings/private" },
  { source: "private-person" },
  { client_id: "invented-identity" },
  { session_id: "123" },
  { session_id: 0 },
  { captured_at: now + 301 },
  { captured_at: now - 30 * 86400 - 1 },
  { campaign: "private-person" },
  { measurement: "disabled" },
])("rejects an invalid or unapproved measurement field: %j", (change) => {
  expect(sanitizeReaderMeasurement({ ...eligible(), ...change }, [], now)).toBeNull();
});

it("requires real existing client and session identifiers together", () => {
  expect(sanitizeReaderMeasurement(eligible(), [], now)).not.toBeNull();
  expect(sanitizeReaderMeasurement({ ...capture(), measurement: "eligible" }, [], now)).toBeNull();
  expect(
    sanitizeReaderMeasurement({ ...capture(), measurement: "unavailable" }, [], now),
  ).not.toBeNull();
});

it("preserves the first landing through navigation and identity-provider returns", () => {
  start();
  const original = currentReaderMeasurement();
  cleanups.pop()!();
  start({
    url: new URL("https://devfeed.tech/login?return_to=/latest"),
    referrer: "https://accounts.google.com/",
  });
  expect(currentReaderMeasurement()).toEqual(original);
  expect(sessionStorage.getItem(acquisitionStorageKey)).not.toContain("accounts.google");
});

it("does not initialize Google Analytics or invent identifiers when its tag is unavailable", async () => {
  start();
  expect(currentReaderMeasurement()?.measurement).toBe("unavailable");
  refreshReaderMeasurement();
  expect(currentReaderMeasurement()?.measurement).toBe("unavailable");
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it("obtains fresh values from the existing deferred tag before recording an original open", async () => {
  start();
  existingTag();
  analytics.gtag = vi.fn((_command, _ga, field, callback) => {
    (callback as (value: string) => void)(field === "client_id" ? "123.456" : "789");
  });
  refreshReaderMeasurement();
  expect(currentReaderMeasurement()).toMatchObject({
    measurement: "eligible",
    client_id: "123.456",
    session_id: 789,
  });
  analytics.gtag = vi.fn((_command, _ga, field, callback) => {
    (callback as (value: string) => void)(field === "client_id" ? "123.456" : "790");
  });
  refreshReaderMeasurement();
  expect(currentReaderMeasurement()).toMatchObject({ session_id: 790 });
});

it("does not reuse stale tag identifiers when a later lookup is blocked", () => {
  vi.useFakeTimers();
  start();
  existingTag();
  analytics.gtag = vi.fn((_command, _ga, field, callback) => {
    (callback as (value: string) => void)(field === "client_id" ? "123.456" : "789");
  });
  refreshReaderMeasurement();
  expect(currentReaderMeasurement()?.measurement).toBe("eligible");
  vi.advanceTimersByTime(60_001);
  analytics.gtag = vi.fn();
  refreshReaderMeasurement();
  expect(currentReaderMeasurement()?.measurement).toBe("unavailable");
});

it("clears capture and cookies on opt-out and ignores delayed Google callbacks", () => {
  start();
  existingTag();
  const callbacks: ((value: string) => void)[] = [];
  analytics.gtag = vi.fn((_command, _ga, _field, callback) =>
    callbacks.push(callback as (value: string) => void),
  );
  cleanups.push(prepareReaderLoginAcquisition());
  refreshReaderMeasurement();
  expect(document.cookie).toContain("devfeed_user_acquisition=");
  analytics["ga-disable-G-READER"] = true;
  window.dispatchEvent(new Event("devfeed:measurement-privacy-changed"));
  callbacks.forEach((callback) => callback("123.456"));
  expect(currentReaderMeasurement()).toMatchObject({
    measurement: "disabled",
    status: "unavailable",
  });
  expect(currentReaderMeasurement()?.client_id).toBeUndefined();
  expect(sessionStorage.getItem(acquisitionStorageKey)).toBeNull();
  expect(document.cookie).not.toContain("devfeed_user_acquisition=");
});

it("survives blocked storage with explicit unavailable context", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage blocked");
  });
  expect(() => start()).not.toThrow();
  expect(currentReaderMeasurement()).toMatchObject({ status: "unavailable" });
  expect(currentReaderMeasurement()?.client_id).toBeUndefined();
});

it("consumes a bounded extension handoff, clears the fragment, and publishes the same platform identity", () => {
  start({ extensionEnabled: true });
  const handoff = { ...eligible(), client_platform: "edge_extension" };
  history.replaceState(
    null,
    "",
    `/login?return_to=%2Fextension%2Flogin-complete#acquisition=${encodeURIComponent(JSON.stringify(handoff))}`,
  );
  cleanups.push(prepareReaderLoginAcquisition());
  expect(location.hash).toBe("");
  expect(currentReaderMeasurement()).toMatchObject({
    client_platform: "edge_extension",
    client_id: "123.456",
    session_id: 789,
  });
  expect(decodeURIComponent(document.cookie)).toContain('"client_platform":"edge_extension"');
});

it("keeps identifiers in the fragment rather than the HTTP login URL", () => {
  start({
    platform: "chrome_extension",
    currentIdentity: () => ({ client_id: "123.456", session_id: 789 }),
  });
  const url = new URL(
    `https://devfeed.tech/login?return_to=/extension/login-complete${readerAcquisitionFragment()}`,
  );
  expect(url.search).not.toContain("123.456");
  expect(JSON.parse(decodeURIComponent(url.hash.slice("#acquisition=".length)))).toMatchObject({
    client_id: "123.456",
  });
  clearReaderAcquisition();
  expect(currentReaderMeasurement()?.status).toBe("unavailable");
});

it("allows an independently enabled extension handoff while web analytics is disabled", () => {
  start({ enabled: false, gaId: "", extensionEnabled: true });
  expect(sessionStorage.getItem(acquisitionStorageKey)).toBeNull();
  const handoff = { ...eligible(), client_platform: "chrome_extension" };
  history.replaceState(
    null,
    "",
    `/login#acquisition=${encodeURIComponent(JSON.stringify(handoff))}`,
  );
  cleanups.push(prepareReaderLoginAcquisition());
  expect(currentReaderMeasurement()).toMatchObject({
    measurement: "eligible",
    client_platform: "chrome_extension",
    client_id: "123.456",
  });
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it("does not borrow web analytics permission for a disabled extension handoff", () => {
  start({ enabled: true, extensionEnabled: false });
  const handoff = { ...eligible(), client_platform: "edge_extension" };
  history.replaceState(
    null,
    "",
    `/login#acquisition=${encodeURIComponent(JSON.stringify(handoff))}`,
  );
  cleanups.push(prepareReaderLoginAcquisition());
  expect(currentReaderMeasurement()).toMatchObject({
    measurement: "disabled",
    status: "unavailable",
  });
  expect(document.cookie).not.toContain("devfeed_user_acquisition=");
});

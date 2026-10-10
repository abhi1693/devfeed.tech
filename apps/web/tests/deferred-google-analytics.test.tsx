// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, it } from "vitest";
import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";

const gaId = "G-N4V5CW5C0M";
const analytics = window as Window & {
  dataLayer?: IArguments[];
  gtag?: (...args: unknown[]) => void;
  "ga-disable-G-N4V5CW5C0M"?: boolean;
};
afterEach(() => {
  cleanup();
  document.querySelectorAll("script[data-devfeed-ga]").forEach((script) => script.remove());
  delete analytics.dataLayer;
  delete analytics.gtag;
  delete analytics["ga-disable-G-N4V5CW5C0M"];
});

it("defers the supplied Google tag and queues the standard commands exactly once", () => {
  render(
    <StrictMode>
      <DeferredGoogleAnalytics gaId={gaId} />
    </StrictMode>,
  );
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
  expect(analytics.dataLayer).toBeUndefined();
  fireEvent.pointerDown(window);
  fireEvent.scroll(window);
  fireEvent.keyDown(window);
  const scripts = document.querySelectorAll<HTMLScriptElement>("script[data-devfeed-ga]");
  expect(scripts).toHaveLength(1);
  expect(scripts[0].src).toBe(`https://www.googletagmanager.com/gtag/js?id=${gaId}`);
  expect(scripts[0].async).toBe(true);
  expect(analytics.dataLayer?.map((command) => Array.from(command))).toEqual([
    ["js", expect.any(Date)],
    ["config", gaId],
  ]);
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  fireEvent.pointerDown(window);
  expect(document.querySelectorAll("script[data-devfeed-ga]")).toHaveLength(1);
  expect(analytics.dataLayer).toHaveLength(2);
});

it("loads on page exit when there was no interaction", () => {
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  fireEvent(window, new Event("pagehide"));
  expect(document.querySelectorAll("script[data-devfeed-ga]")).toHaveLength(1);
});

it("respects the Google Analytics disable flag", () => {
  analytics["ga-disable-G-N4V5CW5C0M"] = true;
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  fireEvent.pointerDown(window);
  fireEvent(window, new Event("pagehide"));
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
  expect(analytics.dataLayer).toBeUndefined();
});

it("removes deferred listeners when unmounted", () => {
  const view = render(<DeferredGoogleAnalytics gaId={gaId} />);
  view.unmount();
  fireEvent.pointerDown(window);
  fireEvent(window, new Event("pagehide"));
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it("queues a first-action event after config and routes it to this GA4 property", async () => {
  const { trackEvent } = await import("@/lib/analytics");
  render(
    <StrictMode>
      <DeferredGoogleAnalytics gaId={gaId} />
    </StrictMode>,
  );
  trackEvent("article_open", { article_id: "article" });
  expect(analytics.dataLayer?.map((command) => Array.from(command))).toEqual([
    ["js", expect.any(Date)],
    ["config", gaId],
    ["event", "article_open", { article_id: "article", send_to: gaId }],
  ]);
  analytics["ga-disable-G-N4V5CW5C0M"] = true;
  trackEvent("article_open", { article_id: "another" });
  expect(analytics.dataLayer).toHaveLength(3);
});

it("does not load analytics for custom events without the production component", async () => {
  const { trackEvent } = await import("@/lib/analytics");
  trackEvent("article_open", { article_id: "article" });
  expect(analytics.dataLayer).toBeUndefined();
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it("queues passive profile views until interaction or exit and retains their public page", async () => {
  const { trackEvent } = await import("@/lib/analytics");
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  const original = location.href;
  history.replaceState(null, "", "/users/reader?utm_source=test#stack");
  trackEvent("dev_card_view", {});
  history.replaceState(null, "", "/latest");
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
  expect(analytics.dataLayer).toBeUndefined();
  fireEvent(window, new Event("pagehide"));
  const commands = analytics.dataLayer?.map((command) => Array.from(command));
  expect(commands).toEqual([
    ["js", expect.any(Date)],
    ["config", gaId],
    [
      "event",
      "dev_card_view",
      { send_to: gaId, page_location: new URL("/users/reader", original).href },
    ],
  ]);
  fireEvent.pointerDown(window);
  expect(analytics.dataLayer).toHaveLength(3);
  history.replaceState(null, "", original);
});
it("flushes passive views before the first custom action and bounds their queue", async () => {
  const { trackEvent } = await import("@/lib/analytics");
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  for (let i = 0; i < 25; i++) trackEvent("dev_card_view", {});
  trackEvent("dev_card_create_click", {});
  const commands = analytics.dataLayer?.map((command) => Array.from(command));
  expect(commands).toHaveLength(23);
  expect(commands?.at(-1)).toEqual(["event", "dev_card_create_click", { send_to: gaId }]);
});
it("honors the disable flag when flushing passive views", async () => {
  const { trackEvent } = await import("@/lib/analytics");
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  trackEvent("dev_card_view", {});
  analytics["ga-disable-G-N4V5CW5C0M"] = true;
  fireEvent.pointerDown(window);
  expect(analytics.dataLayer).toBeUndefined();
});

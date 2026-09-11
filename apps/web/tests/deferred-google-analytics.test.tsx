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
  document.querySelectorAll("script[data-devfeed-ga]").forEach(script => script.remove());
  delete analytics.dataLayer;
  delete analytics.gtag;
  delete analytics["ga-disable-G-N4V5CW5C0M"];
});

it("defers the supplied Google tag and queues the standard commands exactly once", () => {
  render(<StrictMode><DeferredGoogleAnalytics gaId={gaId} /></StrictMode>);
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
  expect(analytics.dataLayer).toBeUndefined();
  fireEvent.pointerDown(window);
  fireEvent.scroll(window);
  fireEvent.keyDown(window);
  const scripts = document.querySelectorAll<HTMLScriptElement>("script[data-devfeed-ga]");
  expect(scripts).toHaveLength(1);
  expect(scripts[0].src).toBe(`https://www.googletagmanager.com/gtag/js?id=${gaId}`);
  expect(scripts[0].async).toBe(true);
  expect(analytics.dataLayer?.map(command => Array.from(command))).toEqual([
    ["js", expect.any(Date)], ["config", gaId],
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
  render(<StrictMode><DeferredGoogleAnalytics gaId={gaId} /></StrictMode>);
  trackEvent("article_open", { article_id: "article" });
  expect(analytics.dataLayer?.map(command => Array.from(command))).toEqual([
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

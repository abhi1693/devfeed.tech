// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";

const gaId = "G-N4V5CW5C0M";
const analytics = window as Window & {
  dataLayer?: IArguments[];
  gtag?: (...args: unknown[]) => void;
  google_tag_manager?: Record<string, unknown>;
  "ga-disable-G-N4V5CW5C0M"?: boolean;
};
afterEach(() => {
  cleanup();
  document.querySelectorAll("script[data-devfeed-ga]").forEach((script) => script.remove());
  delete analytics.dataLayer;
  delete analytics.gtag;
  delete analytics.google_tag_manager;
  document
    .querySelectorAll("script[data-preferred-source-test]")
    .forEach((script) => script.remove());
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

function preferredLink() {
  const view = render(
    <a
      data-google-preferred-source
      href="https://www.google.com/preferences/source?q=devfeed.tech"
      onClick={(event) => event.preventDefault()}
    >
      <span data-testid="preferred-label">Prefer on Google</span>
    </a>,
  );
  return { link: view.getByRole("link"), label: view.getByTestId("preferred-label") };
}

it("delegates a nested preferred-source click to the current GA4 tag without personal fields", () => {
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  const { label } = preferredLink(); // Inserted after listener setup.
  fireEvent.click(label);
  expect(analytics.dataLayer?.map((command) => Array.from(command))).toEqual([
    ["js", expect.any(Date)],
    ["config", gaId],
    ["event", "preferred_source_button_click", { send_to: gaId }],
  ]);
});

it("prioritizes a real GTM container over gtag and emits exactly one content-free object", () => {
  analytics.google_tag_manager = { "GTM-ABC123": {} };
  analytics.gtag = vi.fn();
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  fireEvent.click(preferredLink().label);
  expect(analytics.dataLayer).toEqual([{ event: "preferred_source_button_click" }]);
  expect(analytics.gtag).not.toHaveBeenCalled();
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it.each(["script", "bootstrap"])(
  "recognizes an actual GTM %s before container initialization",
  (kind) => {
    const script = document.createElement("script");
    script.dataset.preferredSourceTest = "true";
    if (kind === "script") script.src = "https://www.googletagmanager.com/gtm.js?id=GTM-ABC123";
    else
      script.textContent =
        "(function(){window.dataLayer=window.dataLayer||[];window.dataLayer.push({'gtm.start':Date.now(),event:'gtm.js'});return 'https://www.googletagmanager.com/gtm.js?id='+'GTM-ABC123';})()";
    document.head.appendChild(script);
    render(<DeferredGoogleAnalytics gaId={gaId} />);
    fireEvent.click(preferredLink().label);
    expect(analytics.dataLayer?.at(-1)).toEqual({ event: "preferred_source_button_click" });
    expect(
      analytics.dataLayer?.filter(
        (entry) =>
          (entry as unknown as { event?: string }).event === "preferred_source_button_click",
      ),
    ).toHaveLength(1);
  },
);

it.each(["dataLayer", "GA4"])("does not mistake %s for a GTM container", (kind) => {
  analytics.dataLayer = [];
  if (kind === "GA4") analytics.google_tag_manager = { [gaId]: {} };
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  fireEvent.click(preferredLink().label);
  expect(analytics.dataLayer?.map((command) => Array.from(command)).at(-1)).toEqual([
    "event",
    "preferred_source_button_click",
    { send_to: gaId },
  ]);
});

it("counts keyboard activation once and removes delegated listeners through StrictMode cleanup", async () => {
  const user = userEvent.setup();
  const view = render(
    <StrictMode>
      <DeferredGoogleAnalytics gaId={gaId} />
    </StrictMode>,
  );
  const { link } = preferredLink();
  link.focus();
  await user.keyboard("{Enter}");
  const commands = () => analytics.dataLayer?.map((command) => Array.from(command));
  expect(
    commands()?.filter((command) => command[1] === "preferred_source_button_click"),
  ).toHaveLength(1);
  view.unmount();
  fireEvent.click(link);
  expect(
    commands()?.filter((command) => command[1] === "preferred_source_button_click"),
  ).toHaveLength(1);
});

it.each(["GTM", "gtag"])(
  "respects the GA disable flag for preferred-source %s delivery",
  (kind) => {
    analytics["ga-disable-G-N4V5CW5C0M"] = true;
    if (kind === "GTM") analytics.google_tag_manager = { "GTM-ABC123": {} };
    render(<DeferredGoogleAnalytics gaId={gaId} />);
    expect(() => fireEvent.click(preferredLink().label)).not.toThrow();
    expect(analytics.dataLayer).toBeUndefined();
    expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
  },
);

it("keeps preferred-source navigation usable without an analytics property or listener", () => {
  render(<DeferredGoogleAnalytics gaId="" />);
  expect(() => fireEvent.click(preferredLink().label)).not.toThrow();
  expect(analytics.dataLayer).toBeUndefined();
  expect(document.querySelector("script[data-devfeed-ga]")).toBeNull();
});

it("keeps one delegated listener for multiple consumers and makes cleanup idempotent", async () => {
  const { analyticsEventName } = await import("@/lib/analytics");
  const { startPreferredSourceAnalytics } = await import("@/lib/preferred-source-analytics");
  const events = vi.fn();
  window.addEventListener(analyticsEventName, events);
  const first = startPreferredSourceAnalytics();
  const second = startPreferredSourceAnalytics();
  try {
    const { link } = preferredLink();
    fireEvent.click(link);
    expect(events).toHaveBeenCalledTimes(1);
    expect((events.mock.calls[0][0] as CustomEvent).detail).toEqual({
      name: "preferred_source_button_click",
      params: {},
    });
    first();
    first();
    fireEvent.click(link);
    expect(events).toHaveBeenCalledTimes(2);
    second();
    fireEvent.click(link);
    expect(events).toHaveBeenCalledTimes(2);
    expect(analytics.dataLayer).toBeUndefined();
  } finally {
    first();
    second();
    window.removeEventListener(analyticsEventName, events);
  }
});

it("ignores unmarked links and marked links to another source", () => {
  render(<DeferredGoogleAnalytics gaId={gaId} />);
  const { link } = preferredLink();
  link.removeAttribute("data-google-preferred-source");
  fireEvent.click(link);
  link.setAttribute("data-google-preferred-source", "");
  link.setAttribute("href", "https://www.google.com/preferences/source?q=other.example");
  fireEvent.click(link);
  expect(analytics.dataLayer).toBeUndefined();
});

"use client";

import { useEffect } from "react";
import { analyticsEventName, type AnalyticsEvent } from "@/lib/analytics";

type GtagCommand = [string, ...unknown[]];
type Gtag = (...args: GtagCommand) => void;

interface AnalyticsWindow extends Window {
  dataLayer?: IArguments[];
  gtag?: Gtag;
}

function loadGoogleAnalytics(gaId: string) {
  const disabledKey = `ga-disable-${gaId}`;
  const analyticsWindow = window as AnalyticsWindow;
  const analyticsFlags = window as unknown as Record<string, unknown>;

  if (analyticsFlags[disabledKey]) return;
  if (document.querySelector(`script[data-devfeed-ga="${gaId}"]`)) return analyticsWindow.gtag;

  analyticsWindow.dataLayer = analyticsWindow.dataLayer ?? [];
  analyticsWindow.gtag =
    analyticsWindow.gtag ??
    function gtag() {
      // gtag.js expects queued commands to match the standard snippet shape.
      // eslint-disable-next-line prefer-rest-params
      analyticsWindow.dataLayer?.push(arguments);
    };

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(gaId)}`;
  script.dataset.devfeedGa = gaId;
  document.head.appendChild(script);

  analyticsWindow.gtag("js", new Date());
  analyticsWindow.gtag("config", gaId);
  return analyticsWindow.gtag;
}

export function DeferredGoogleAnalytics({ gaId }: { gaId: string }) {
  useEffect(() => {
    let loaded = false;
    const pendingViews: string[] = [];

    const loadOnce = () => {
      const gtag = loadGoogleAnalytics(gaId);
      if (!loaded) {
        loaded = true;
        for (const pageLocation of pendingViews.splice(0))
          gtag?.("event", "dev_card_view", { send_to: gaId, page_location: pageLocation });
      }
      return gtag;
    };

    const handleEvent = (event: Event) => {
      try {
        const { name, params } = (event as CustomEvent<AnalyticsEvent>).detail;
        // Passive profile views must not bypass first-interaction deferral. Keep the
        // original public page (without query/hash) when flushing after navigation.
        if (name === "dev_card_view" && !loaded) {
          pendingViews.push(new URL(location.pathname, location.origin).href);
          if (pendingViews.length > 20) pendingViews.shift();
          return;
        }
        // First-action events initialize config and flush passive views first.
        const gtag = loadOnce();
        gtag?.("event", name, { ...params, send_to: gaId });
      } catch {
        /* Tracking failure must not interrupt user actions. */
      }
    };
    window.addEventListener(analyticsEventName, handleEvent);

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        loadOnce();
      }
    };

    const events: Array<keyof WindowEventMap> = ["pointerdown", "keydown", "scroll", "touchstart"];

    events.forEach((eventName) => {
      window.addEventListener(eventName, loadOnce, {
        once: true,
        passive: true,
      });
    });
    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pagehide", loadOnce, { once: true });

    return () => {
      window.removeEventListener(analyticsEventName, handleEvent);
      events.forEach((eventName) => {
        window.removeEventListener(eventName, loadOnce);
      });
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pagehide", loadOnce);
    };
  }, [gaId]);

  return null;
}

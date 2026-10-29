"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { trackEvent } from "@/lib/analytics";
import { readerIsWebsite } from "@/lib/reader-runtime";

function subscribeTheme(changed: () => void) {
  const observer = new MutationObserver(changed);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

function PreferredSourceWidget({ theme }: { theme: "light" | "dark" }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(52);
  useEffect(() => {
    const clicked = (event: MessageEvent) => {
      const widget = frame.current;
      if (
        !widget ||
        event.source !== widget.contentWindow ||
        event.origin !== new URL(widget.src).origin
      )
        return;
      if (event.data?.type === "devfeed:preferred-source-click")
        trackEvent("preferred_source_button_click", {});
      else if (
        event.data?.type === "devfeed:preferred-source-size" &&
        Number.isInteger(event.data.height) &&
        event.data.height >= 52 &&
        event.data.height <= 400
      )
        setHeight(event.data.height);
    };
    window.addEventListener("message", clicked);
    return () => window.removeEventListener("message", clicked);
  }, []);
  return (
    <iframe
      ref={frame}
      className="google-preferred-source-widget"
      src={`/preferred-source/widget?theme=${theme}`}
      title="Add DevFeed as a preferred source on Google"
      width={240}
      height={height}
      data-expanded={height > 52 ? "true" : undefined}
      style={{ colorScheme: theme }}
    />
  );
}

/** Google confirms the reader's choice; this control measures only opening that flow. */
export function GooglePreferredSource() {
  const website = readerIsWebsite();
  const dark = useSyncExternalStore(
    subscribeTheme,
    () => document.documentElement.classList.contains("dark"),
    () => false,
  );
  const theme = dark ? "dark" : "light";
  if (website) return <PreferredSourceWidget key={theme} theme={theme} />;

  return (
    <a
      className="google-preferred-source-badge"
      data-google-preferred-source=""
      href="https://www.google.com/preferences/source?q=devfeed.tech"
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Add DevFeed as a preferred source on Google (opens in a new tab)"
    >
      {/* Google's packaged artwork is also available when the extension is offline. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/google-preferred-source/${theme}.webp`}
        width={676}
        height={213}
        alt="Add DevFeed as a preferred source on Google"
      />
    </a>
  );
}

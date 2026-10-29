import { trackEvent } from "./analytics";

const preferredSourceUrl = "https://www.google.com/preferences/source?q=devfeed.tech";
const containerId = /^GTM-[A-Z0-9]+$/;
let listeners = 0;

function clicked(event: MouseEvent) {
  const link =
    event.target instanceof Element
      ? event.target.closest<HTMLAnchorElement>("a[data-google-preferred-source]")
      : null;
  if (event.button === 0 && link?.href === preferredSourceUrl)
    trackEvent("preferred_source_button_click", {});
}

/** One delegated click listener also covers links rendered after startup. */
export function startPreferredSourceAnalytics() {
  if (typeof document === "undefined") return () => {};
  if (listeners++ === 0) document.addEventListener("click", clicked);
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    if (--listeners === 0) document.removeEventListener("click", clicked);
  };
}

/** A GA4 gtag/dataLayer alone does not indicate a Tag Manager container. */
export function hasGoogleTagManager() {
  const manager = (window as Window & { google_tag_manager?: Record<string, unknown> })
    .google_tag_manager;
  if (manager && Object.keys(manager).some((key) => containerId.test(key))) return true;
  return Array.from(document.scripts).some((script) => {
    if (script.src) {
      try {
        const url = new URL(script.src, document.baseURI);
        return (
          url.origin === "https://www.googletagmanager.com" &&
          url.pathname === "/gtm.js" &&
          containerId.test(url.searchParams.get("id") ?? "")
        );
      } catch {
        return false;
      }
    }
    // The standard inline bootstrap may precede its external script insertion.
    const code = script.textContent ?? "";
    return (
      code.includes("googletagmanager.com/gtm.js") &&
      code.includes("gtm.start") &&
      /["']GTM-[A-Z0-9]+["']/.test(code)
    );
  });
}

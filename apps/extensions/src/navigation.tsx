import { useSyncExternalStore } from "react";
import { publicOrigin } from "./transport";
import { extensionRoute } from "./routes";

export function isLocalRoute(href: string) {
  const url = new URL(href, publicOrigin);
  return url.origin === publicOrigin && extensionRoute(url.pathname) !== null;
}

export function linkDestination(href: string) {
  const url = new URL(href, publicOrigin);
  if (!["https:", "http:", "mailto:"].includes(url.protocol)) return "#";
  return isLocalRoute(href) ? `#${url.pathname}${url.search}` : url.href;
}

const changed = "devfeed:extension-route";
const snapshot = () => window.location.hash.slice(1) || "/";
function subscribe(listener: () => void) {
  window.addEventListener("hashchange", listener);
  window.addEventListener(changed, listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener(changed, listener);
  };
}
export function useRoute() {
  return useSyncExternalStore(subscribe, snapshot, () => "/");
}
function navigate(href: string, replace = false, scroll = true) {
  const destination = linkDestination(href);
  if (!destination.startsWith("#")) {
    window.open(destination, "_blank", "noopener,noreferrer");
    return;
  }
  const article = destination.startsWith("#/articles/");
  const background = article
    ? (window.history.state?.readerBackground ??
      (snapshot().startsWith("/articles/") ? undefined : snapshot()))
    : undefined;
  if (destination === `#${snapshot()}`) router.refresh();
  window.history[replace ? "replaceState" : "pushState"](
    { readerBackground: background },
    "",
    destination,
  );
  window.dispatchEvent(new Event(changed));
  if (scroll) window.scrollTo(0, 0);
}
const router = {
  push: (href: string, options?: { scroll?: boolean }) => navigate(href, false, options?.scroll),
  replace: (href: string, options?: { scroll?: boolean }) => navigate(href, true, options?.scroll),
  refresh: () => window.dispatchEvent(new Event("devfeed:extension-refresh")),
  back: () => window.history.back(),
  forward: () => window.history.forward(),
  prefetch: async () => {},
};
export const useRouter = () => router;
export const usePathname = () => new URL(useRoute(), publicOrigin).pathname;
export const useSearchParams = () => new URL(useRoute(), publicOrigin).searchParams;

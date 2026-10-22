import { useSyncExternalStore } from "react";
import { publicOrigin } from "./transport";
import {
  resolveReaderDestination,
  type ReaderArticleDestination,
  type ReaderDestination,
  type ReaderNavigationMode,
  type ReaderPageDestination,
} from "../../web/src/lib/reader-destination";
import { saveHistoryScroll } from "../../web/src/lib/history-scroll";

export type ReaderNavigationState =
  | { kind: "page"; destination: ReaderPageDestination }
  | {
      kind: "article";
      destination: ReaderArticleDestination;
      background: ReaderPageDestination | null;
    };

type ExtensionLink = { href: string; destination: ReaderDestination | null };

/** Generic anchor URLs are decoded only here, before entering typed navigation. */
export function resolveExtensionLink(href: string): ExtensionLink {
  try {
    const url = new URL(href, publicOrigin);
    if (url.protocol === "mailto:") return { href: url.href, destination: null };
    if (!["https:", "http:"].includes(url.protocol)) return { href: "#", destination: null };
    const destination = resolveReaderDestination(url.href, { base: publicOrigin });
    const local =
      url.origin === publicOrigin && (destination.kind === "article" || destination.route !== null);
    return local
      ? { href: `#${destination.href}`, destination }
      : { href: url.href, destination: null };
  } catch {
    return { href: "#", destination: null };
  }
}

export function isLocalRoute(href: string) {
  return resolveExtensionLink(href).destination !== null;
}

export function linkDestination(href: string) {
  return resolveExtensionLink(href).href;
}

export function destinationHref(destination: ReaderDestination) {
  const url = new URL(destination.href, publicOrigin);
  return url.origin === publicOrigin &&
    (destination.kind === "article" || destination.route !== null)
    ? `#${destination.href}`
    : url.href;
}

function savedPage(value: unknown): ReaderPageDestination | null {
  if (!value || typeof value !== "object") return null;
  const saved = value as Partial<ReaderPageDestination>;
  if (saved.kind !== "page" || typeof saved.href !== "string") return null;
  try {
    const url = new URL(saved.href, publicOrigin);
    if (url.origin !== publicOrigin || url.username || url.password) return null;
    const destination = resolveReaderDestination(url.href, { base: publicOrigin });
    if (destination.kind !== "page") return null;
    return { ...destination, scroll: saved.scroll === "preserve" ? "preserve" : "reset" };
  } catch {
    return null;
  }
}

/** Hydrate browser entries at the URL boundary, without trusting serialized descriptors. */
export function resolveReaderNavigation(route: string, value: unknown): ReaderNavigationState {
  const destination = resolveReaderDestination(route, { base: publicOrigin });
  const saved =
    value && typeof value === "object" ? (value as Partial<ReaderNavigationState>) : null;
  const matching = saved?.kind === destination.kind && saved.destination?.href === destination.href;
  if (destination.kind === "page")
    return {
      kind: "page",
      destination:
        matching && saved?.destination?.scroll === "preserve"
          ? { ...destination, scroll: "preserve" }
          : destination,
    };
  const background =
    matching && saved?.kind === "article" && saved.destination?.slug === destination.slug
      ? savedPage(saved.background)
      : null;
  return { kind: "article", destination, background };
}

const changed = "devfeed:extension-route";
const routeSnapshot = () => {
  const route = window.location.hash.slice(1) || "/";
  if (!route.startsWith("/") || route.startsWith("//") || route.includes("\\"))
    return "/__missing__";
  return route;
};

/** Session and preview checks use the same validated location as the router. */
export const readerRouteLocation = () => new URL(routeSnapshot(), publicOrigin);

let cachedRoute: string | undefined;
let cachedSaved: unknown;
let cachedState: ReaderNavigationState;
function navigationSnapshot() {
  const route = routeSnapshot();
  const saved = window.history.state?.readerNavigation;
  // Scroll saves replace the outer history object but retain this navigation
  // record. Decode only a new browser entry, never every rendered snapshot.
  if (route !== cachedRoute || saved !== cachedSaved) {
    cachedState = resolveReaderNavigation(route, saved);
    cachedRoute = route;
    cachedSaved = saved;
  }
  return cachedState;
}
function subscribe(listener: () => void) {
  window.addEventListener("hashchange", listener);
  window.addEventListener("popstate", listener);
  window.addEventListener(changed, listener);
  return () => {
    window.removeEventListener("hashchange", listener);
    window.removeEventListener("popstate", listener);
    window.removeEventListener(changed, listener);
  };
}
const serverState = resolveReaderNavigation("/", undefined);
export function useNavigationState() {
  return useSyncExternalStore(subscribe, navigationSnapshot, () => serverState);
}
export function useRoute() {
  return useNavigationState().destination.href;
}

export function navigate(destination: ReaderDestination, mode: ReaderNavigationMode = "push") {
  const href = destinationHref(destination);
  if (!href.startsWith("#")) {
    window.open(href, "_blank", "noopener,noreferrer");
    return;
  }
  const current = navigationSnapshot();
  const state: ReaderNavigationState =
    destination.kind === "article"
      ? {
          kind: "article",
          destination,
          background: current.kind === "article" ? current.background : current.destination,
        }
      : { kind: "page", destination };
  if (destination.href === current.destination.href) router.refresh();
  saveHistoryScroll();
  const scroll = destination.scroll === "reset";
  window.history[mode === "replace" ? "replaceState" : "pushState"](
    { readerNavigation: state, readerScroll: scroll ? [0, 0] : [window.scrollX, window.scrollY] },
    "",
    href,
  );
  cachedState = state;
  cachedRoute = routeSnapshot();
  cachedSaved = window.history.state?.readerNavigation;
  window.dispatchEvent(new Event(changed));
  if (scroll) window.scrollTo(0, 0);
}

function navigateLink(href: string, mode: ReaderNavigationMode, options?: { scroll?: boolean }) {
  const link = resolveExtensionLink(href);
  if (!link.destination) {
    if (link.href !== "#") window.open(link.href, "_blank", "noopener,noreferrer");
    return;
  }
  navigate(
    link.destination.kind === "page" && options?.scroll === false
      ? { ...link.destination, scroll: "preserve" }
      : link.destination,
    mode,
  );
}
const router = {
  push: (href: string, options?: { scroll?: boolean }) => navigateLink(href, "push", options),
  replace: (href: string, options?: { scroll?: boolean }) => navigateLink(href, "replace", options),
  refresh: () => window.dispatchEvent(new Event("devfeed:extension-refresh")),
  back: () => window.history.back(),
  forward: () => window.history.forward(),
  prefetch: async () => {},
};
export const useRouter = () => router;
export const usePathname = () => new URL(useRoute(), publicOrigin).pathname;
export const useSearchParams = () => new URL(useRoute(), publicOrigin).searchParams;

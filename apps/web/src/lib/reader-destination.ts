import { isArticleSlug, resolveReaderRoute, type ReaderRoute } from "./reader-routes";
import {
  readerSignInDestination,
  type ReaderDestinationRecord,
} from "@devfeed/http/reader-destinations";

export type ReaderScroll = "reset" | "preserve";
export type ReaderPageRoute = Exclude<ReaderRoute, { type: "article" }>;
export type ReaderPageDestination = {
  kind: "page";
  href: string;
  route: ReaderPageRoute | null;
  scroll: ReaderScroll;
};
export type ReaderArticleDestination = {
  kind: "article";
  slug: string;
  href: string;
  scroll: "preserve";
};
export type ReaderDestination = ReaderPageDestination | ReaderArticleDestination;
export type ReaderNavigationMode = "push" | "replace";

export function readerReturnDestination(
  value: string | ReaderDestination | ReaderDestinationRecord,
): string {
  return readerSignInDestination(
    typeof value === "string" || !("href" in value) ? value : value.href,
  );
}

export function articleDestination(slug: string): ReaderArticleDestination {
  if (!isArticleSlug(slug)) throw new TypeError("Invalid article identifier");
  return {
    kind: "article",
    slug,
    href: `/articles/${encodeURIComponent(slug)}`,
    scroll: "preserve",
  };
}

/** Decode generic links at the navigation boundary; consumers retain the resolved identity. */
export function resolveReaderDestination(
  href: string,
  {
    base = "https://reader.invalid",
    scroll = "reset",
  }: { base?: string | URL; scroll?: ReaderScroll } = {},
): ReaderDestination {
  const origin = new URL(base);
  const url = new URL(href, origin);
  if (!["http:", "https:"].includes(url.protocol))
    throw new TypeError("Unsupported reader destination protocol");
  const local = url.origin === origin.origin;
  const route = local ? resolveReaderRoute(url.pathname) : null;
  const destination = local ? url.pathname + url.search + url.hash : url.href;
  return route?.type === "article"
    ? { ...articleDestination(route.slug), href: destination }
    : { kind: "page", href: destination, route, scroll };
}

export function pageDestination(
  href: string,
  scroll: ReaderScroll = "reset",
): ReaderPageDestination {
  const destination = resolveReaderDestination(href, { scroll });
  if (destination.kind !== "page") throw new TypeError("Expected a page destination");
  return destination;
}

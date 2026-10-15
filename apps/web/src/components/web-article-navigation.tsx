"use client";

import { useSelectedLayoutSegments } from "next/navigation";
import { ArticleNavigationProvider, type ArticleOverlay } from "./article-navigation";

function articleSlug(segments: string[], articleSegment: "articles" | "(.)articles") {
  // Framework layout groups precede the registered route; interception markers are route names.
  const route = segments.findIndex(
    (segment) => !(segment.startsWith("(") && segment.endsWith(")")),
  );
  return segments[route] === articleSegment ? segments[route + 1] : undefined;
}

/** Read the registered framework slots once; retained pages do not own the visible overlay. */
export function WebArticleNavigationProvider({ children }: { children: React.ReactNode }) {
  const page = useSelectedLayoutSegments();
  const modal = useSelectedLayoutSegments("modal");
  const directSlug = articleSlug(page, "articles");
  const interceptedSlug = articleSlug(modal, "(.)articles");
  const overlay: ArticleOverlay | null = interceptedSlug
    ? { slug: interceptedSlug, entry: "intercepted", dismiss: directSlug ? "feed" : "history" }
    : directSlug
      ? { slug: directSlug, entry: "direct", dismiss: "feed" }
      : null;
  return <ArticleNavigationProvider overlay={overlay}>{children}</ArticleNavigationProvider>;
}

import { resolveReaderDestination } from "./reader-destination";

export const articlePreviewOpened = "devfeed:article-preview-opened";

/** Only an in-memory preview opened over this document can retain its background. */
export function createPreviewHistory() {
  let preview: { background: string; article: string; active: boolean; committed: boolean } | null =
    null;
  return {
    navigate(from: URL, href: string) {
      let target: URL;
      try {
        target = new URL(href, from);
      } catch {
        preview = null;
        return;
      }
      if (target.origin !== from.origin) {
        preview = null;
        return;
      }
      const destination = resolveReaderDestination(href, { base: from });
      if (destination.kind !== "article") {
        preview = null;
        return;
      }
      const current = resolveReaderDestination(from.href, { base: from });
      if (current.kind === "page" && current.route) {
        preview = { background: from.href, article: target.href, active: true, committed: false };
      } else if (preview?.active && preview.article === from.href) {
        preview.article = target.href;
        preview.committed = false;
      } else preview = null;
    },
    commit(article: URL) {
      if (preview?.active && article.href === preview.article) preview.committed = true;
      else preview = null;
    },
    restore(target: URL) {
      if (preview?.active && preview.committed && target.href === preview.background) {
        preview.active = false;
        return true;
      }
      if (preview && !preview.active && target.href === preview.article) {
        // Forward still reauthorizes normally before this preview can return again.
        preview.active = true;
        preview.committed = false;
        return false;
      }
      preview = null;
      return false;
    },
    reset() {
      preview = null;
    },
  };
}

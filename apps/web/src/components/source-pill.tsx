import { sourceHref } from "@/lib/feed-query";
import Link from "next/link";
import type { Source } from "@/lib/types";
import { CatalogIcon } from "./catalog-icon";

/** A source keeps the same accent across cards, previews and compact rows. */
export function SourcePill({ source, fallback }: { source?: Source; fallback: string }) {
  const name = source?.name ?? fallback;
  const identity = source?.id ?? fallback;
  let hash = 0;
  for (const character of identity) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  const content = (
    <>
      <CatalogIcon url={source?.logo_url ?? null} source />
      <span className="source-pill-name">{name}</span>
    </>
  );
  const props = { className: "source-pill", "data-tone": hash % 6, title: name };
  return source ? (
    <Link {...props} href={sourceHref(source)}>
      {content}
    </Link>
  ) : (
    <span {...props}>{content}</span>
  );
}

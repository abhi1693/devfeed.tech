import Link from "next/link";
import type { Source } from "@/lib/types";
import { sourceHref } from "@/lib/feed-query";
import { CatalogIcon } from "./catalog-icon";
import { SourceFollow } from "./source-follow";

export function ArticleSourceBrief({
  source,
  articleSlug,
}: {
  source: Source;
  articleSlug: string;
}) {
  return (
    <section className="topic-brief source-brief" aria-label={`About ${source.name}`}>
      <p className="topic-brief-label">About this source</p>
      <div className="topic-brief-heading">
        <CatalogIcon url={source.logo_url} source />
        <h2>
          <Link href={sourceHref(source)}>{source.name}</Link>
        </h2>
      </div>
      {source.description && <p className="topic-brief-description">{source.description}</p>}
      <div className="topic-brief-actions">
        <SourceFollow sourceId={source.id} returnTo={`/articles/${articleSlug}`} />
      </div>
    </section>
  );
}

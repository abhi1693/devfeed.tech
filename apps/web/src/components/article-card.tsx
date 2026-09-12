import { UserDate } from "./user-date";
import Link from "next/link";
import type { Article } from "@/lib/types";
import { displayHost, sourceHref, safeExternalUrl } from "@/lib/feed-query";

import { ArticleEngagement } from "./article-engagement";
import { CatalogIcon } from "./catalog-icon";
import { ArticleImage } from "./article-image";

export function ArticleCard({
  article,
  priority = false,
  recommendation,
}: {
  article: Article;
  priority?: boolean;
  recommendation?: string;
}) {
  const href = `/articles/${article.slug}`;
  const image = safeExternalUrl(article.image_url);
  const source = article.sources[0];
  return (
    <article className="article-card">
      <div className="card-image" aria-hidden="true">
        <ArticleImage
          src={image}
          priority={priority}
          label={article.topics[0]?.name ?? "Article"}
        />
      </div>

      <div className="card-copy">
        <div className="source-row">
          <span className="source-avatar" aria-hidden="true">
            <CatalogIcon url={source?.logo_url ?? null} source />
          </span>
          {source ? (
            <Link className="source-name" href={sourceHref(source)}>
              {source.name}
            </Link>
          ) : (
            <span className="source-name">{displayHost(article.canonical_url)}</span>
          )}
          <span className="content-type" data-content-type={article.content_type}>
            {article.content_type}
          </span>
        </div>
        <h2>
          <Link href={href} scroll={false} prefetch={false} className="card-open-link">
            {article.title}
          </Link>
        </h2>
        <div className="card-tags">
          {article.topics.slice(0, 2).map((topic) => (
            <Link href={`/topics/${encodeURIComponent(topic.slug)}`} key={topic.id}>
              #{topic.name}
            </Link>
          ))}
        </div>
        {recommendation && <p className="recommendation-reason">{recommendation}</p>}
        <div className="card-bottom">
          <UserDate value={article.published_at ?? article.feed_at} />
          <ArticleEngagement articleId={article.id} articleSlug={article.slug} />
        </div>
      </div>
    </article>
  );
}

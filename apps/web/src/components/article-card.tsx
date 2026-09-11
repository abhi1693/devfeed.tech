import Link from "next/link";
import type { Article } from "@/lib/types";
import { displayDate, displayHost, safeExternalUrl } from "@/lib/feed-query";

import { ArticleEngagement } from "./article-engagement";
import { CatalogIcon } from "./catalog-icon";
import { ArticleImage } from "./article-image";

export function ArticleCard({
  article,
  priority = false,
}: {
  article: Article;
  priority?: boolean;
}) {
  const href = `/articles/${article.id}`;
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
            <Link className="source-name" href={`/sources/${source.id}`}>
              {source.name}
            </Link>
          ) : (
            <span className="source-name">
              {displayHost(article.canonical_url)}
            </span>
          )}
          <span className="content-type">{article.content_type}</span>
        </div>
        <h2>
          <Link
            href={href}
            scroll={false}
            prefetch={false}
            className="card-open-link"
          >
            {article.title}
          </Link>
        </h2>
        <div className="card-tags">
          {article.topics.slice(0, 2).map((topic) => (
            <Link
              href={`/topics/${encodeURIComponent(topic.slug)}`}
              key={topic.id}
            >
              #{topic.name}
            </Link>
          ))}
        </div>
        <div className="card-bottom">
          <time dateTime={article.published_at ?? article.feed_at}>
            {displayDate(article.published_at ?? article.feed_at)}
          </time>
          <ArticleEngagement articleId={article.id} />
        </div>
      </div>
    </article>
  );
}

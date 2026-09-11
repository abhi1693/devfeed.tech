import Link from "next/link";
import { ArrowUpRight, ChevronDown, Sparkles } from "lucide-react";
import type { Article } from "@/lib/types";
import { displayDate, displayHost, safeExternalUrl } from "@/lib/feed-query";
import { EngagementProvider, ArticleEngagement } from "./article-engagement";
import { ArticleImage } from "./article-image";
import { CatalogIcon } from "./catalog-icon";

export function ArticlePreview({ article }: { article: Article }) {
  const original = safeExternalUrl(article.canonical_url);
  const cover = safeExternalUrl(article.image_url);
  const source = article.sources[0];
  const overview = article.ai_summary || article.summary;
  const showExcerpt =
    article.ai_summary &&
    article.summary &&
    article.summary.trim() !== article.ai_summary.trim();
  return (
    <EngagementProvider articleIds={[article.id]}>
      <article className="article-preview preview-modal">
        <div className="preview-scroll">
          <div className="preview-layout">
            <div className="preview-copy">
              <div className="preview-publisher">
                <CatalogIcon url={source?.logo_url ?? null} source />
                <div>
                  {source ? (
                    <Link href={`/sources/${source.id}`}>{source.name}</Link>
                  ) : (
                    <span>{displayHost(article.canonical_url)}</span>
                  )}
                  <div className="preview-date">
                    <time dateTime={article.published_at ?? article.feed_at}>
                      {displayDate(article.published_at ?? article.feed_at)}
                    </time>
                    <span aria-hidden="true">·</span>
                    <span>{article.content_type}</span>
                  </div>
                </div>
              </div>
              <h1 id="article-preview-title">{article.title}</h1>
              {article.author && (
                <p className="preview-author">By {article.author}</p>
              )}
              {!!article.topics.length && (
                <div className="preview-topics">
                  {article.topics.map((topic) => (
                    <Link
                      key={topic.id}
                      href={`/topics/${encodeURIComponent(topic.slug)}`}
                    >
                      {topic.name}
                    </Link>
                  ))}
                </div>
              )}
              {overview && (
                <section className="preview-summary">
                  <h2>
                    {article.ai_summary && (
                      <Sparkles size={15} aria-hidden="true" />
                    )}
                    {article.ai_summary ? "AI overview" : "Overview"}
                  </h2>
                  <p>{overview}</p>
                </section>
              )}
              {cover && (
                <div className="preview-cover">
                  <ArticleImage
                    src={cover}
                    sizes="(max-width: 700px) calc(100vw - 64px), 420px"
                    label={article.topics[0]?.name ?? "Article"}
                  />
                </div>
              )}
              {showExcerpt && (
                <details className="preview-excerpt">
                  <summary>
                    Source excerpt
                    <ChevronDown size={15} aria-hidden="true" />
                  </summary>
                  <p>{article.summary}</p>
                </details>
              )}
              {!article.topics.length && !!article.tags.length && (
                <div className="preview-topics">
                  {article.tags.map((tag) => (
                    <Link key={tag} href={`/?tag=${encodeURIComponent(tag)}`}>
                      #{tag}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
        <footer className="preview-actions">
          <ArticleEngagement articleId={article.id} trackOpen />
          {original && (
            <a
              className="button primary"
              href={original}
              target="_blank"
              rel="noopener noreferrer"
              title={`Read on ${displayHost(article.canonical_url)}`}
            >
              Read article
              <ArrowUpRight size={17} />
            </a>
          )}
        </footer>
      </article>
    </EngagementProvider>
  );
}

import type { ReactNode } from "react";
import { ArticleShare } from "./article-share";
import { UserDate } from "./user-date";
import { Markdown } from "@devfeed/ui/markdown";
import Link from "next/link";
import { ArrowUpRight, ChevronDown, Sparkles } from "lucide-react";
import type { Article } from "@/lib/types";
import { displayHost, sourceHref, outboundArticleUrl, safeExternalUrl } from "@/lib/feed-query";
import {
  EngagementProvider,
  ArticleEngagement,
  ArticleReadLink,
  ArticleBookmarkButton,
} from "./article-engagement";
import { ArticleImage } from "./article-image";
import { CatalogIcon } from "./catalog-icon";
import { ArticleSourceBrief } from "./article-source-brief";

export function ArticlePreviewContent({
  article,
  topicBrief,
  children,
}: {
  article: Article;
  topicBrief?: ReactNode;
  children?: ReactNode;
}) {
  const original = outboundArticleUrl(article.canonical_url);
  const cover = safeExternalUrl(article.image_url);
  const source = article.sources[0];
  const overview = article.ai_summary || article.summary;
  const showExcerpt =
    article.ai_summary && article.summary && article.summary.trim() !== article.ai_summary.trim();
  return (
    <EngagementProvider articleIds={[article.id]}>
      {children}
      <article className="article-preview preview-modal">
        <div className="preview-scroll">
          <div className="preview-layout">
            <div className="preview-copy">
              <div className="preview-publisher">
                <CatalogIcon url={source?.logo_url ?? null} source />
                <div>
                  {source ? (
                    <Link href={sourceHref(source)}>{source.name}</Link>
                  ) : (
                    <span>{displayHost(article.canonical_url)}</span>
                  )}
                  <div className="preview-date">
                    <UserDate value={article.published_at ?? article.feed_at} />
                    <span aria-hidden="true">·</span>
                    <span>{article.content_type}</span>
                  </div>
                </div>
                {original && (
                  <ArticleReadLink
                    articleId={article.id}
                    className="button primary preview-read-button"
                    href={original}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`Read on ${displayHost(article.canonical_url)}`}
                  >
                    Read article
                    <ArrowUpRight size={17} aria-hidden="true" />
                  </ArticleReadLink>
                )}
              </div>
              <h1 id="article-preview-title">{article.title}</h1>
              {article.author && <p className="preview-author">By {article.author}</p>}
              {overview && (
                <section className="preview-summary">
                  <h2>
                    {article.ai_summary && <Sparkles size={15} aria-hidden="true" />}
                    {article.ai_summary ? "AI overview" : "Overview"}
                  </h2>
                  <Markdown>{overview}</Markdown>
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
                  <Markdown>{article.summary}</Markdown>
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
            <aside className="preview-sidebar">
              {source && <ArticleSourceBrief source={source} articleSlug={article.slug} />}
              {topicBrief}
            </aside>
          </div>
        </div>
        <footer className="preview-footer" aria-label="Article actions">
          <div className="preview-footer-content">
            <div className="preview-footer-toolbar" role="group" aria-label="Article interactions">
              <ArticleEngagement articleId={article.id} articleSlug={article.slug} />
              <ArticleBookmarkButton articleId={article.id} articleSlug={article.slug} label />
              <ArticleShare key={article.id} slug={article.slug} title={article.title} label />
            </div>
          </div>
        </footer>
      </article>
    </EngagementProvider>
  );
}

import { articleAction, isVideo } from "@/lib/video";
import { VideoMetadata } from "./video-metadata";
import "../app/styles/article.css";
import type { ReactNode } from "react";
import { ArticleShare } from "./article-share";
import { UserDate } from "./user-date";
import { Markdown } from "@devfeed/ui/markdown";
import Link from "@/components/reader-link";
import { ArrowUpRight, ChevronDown, Sparkles } from "lucide-react";
import type { Article } from "@/lib/types";
import { displayHost, sourceHref, safeExternalUrl } from "@/lib/feed-query";
import {
  EngagementProvider,
  ArticleEngagement,
  ArticleReadLink,
  ArticleBookmarkButton,
} from "./article-engagement";
import { ArticleImage } from "./article-image";
import { CatalogIcon } from "./catalog-icon";
import { ArticleSourceBrief } from "./article-source-brief";
import { PartnerPlacements } from "./partner-placements";
import { ArticleSponsorship } from "./article-sponsorship";
import { adLinkRel } from "@devfeed/ui/ad-link-rel";

export function ArticlePreviewContent({
  article,
  topicBrief,
  children,
}: {
  article: Article;
  topicBrief?: ReactNode;
  children?: ReactNode;
}) {
  const action = articleAction(article);
  const original = action.href;
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
                <CatalogIcon
                  url={source?.logo_url ?? null}
                  variants={source?.logo_variants}
                  displaySize={23}
                  source
                />
                <div>
                  {source ? (
                    <Link href={sourceHref(source)}>{source.name}</Link>
                  ) : (
                    <span>{displayHost(article.canonical_url)}</span>
                  )}
                  <div className="preview-date">
                    <UserDate value={article.published_at ?? article.feed_at} />
                  </div>
                </div>
                {original && (
                  <ArticleReadLink
                    articleId={article.id}
                    className="button primary preview-read-button"
                    href={original}
                    target="_blank"
                    rel={
                      article.sponsor_names?.length
                        ? adLinkRel(article.sponsor_link_rel)
                        : "noopener noreferrer"
                    }
                    title={
                      isVideo(article)
                        ? action.accessibleLabel
                        : `Read on ${displayHost(article.canonical_url)}`
                    }
                    aria-label={isVideo(article) ? action.accessibleLabel : undefined}
                  >
                    {isVideo(article) ? action.label : `Read ${article.content_type}`}
                    <ArrowUpRight size={17} aria-hidden="true" />
                  </ArticleReadLink>
                )}
              </div>
              <h1 id="article-preview-title">{article.title}</h1>
              <VideoMetadata content={article} />
              {isVideo(article) && (
                <p className="preview-watch-note">
                  Watch links count as original opens in your activity. Opening a link does not
                  confirm playback or completion.
                </p>
              )}
              <ArticleSponsorship names={article.sponsor_names} />
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
                    variants={article.image_variants}
                    priority
                    src={cover}
                    sizes="(max-width: 700px) calc(100vw - 64px), 420px"
                    label={article.topics[0]?.name ?? "Article"}
                  />
                </div>
              )}
              {showExcerpt && (
                <details className="preview-excerpt">
                  <summary>
                    {isVideo(article) ? "Video description" : "Source excerpt"}
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
            <div className="preview-sidebar" role="complementary" data-nosnippet>
              {(isVideo(article) ? article.sources : source ? [source] : []).map((item) => (
                <ArticleSourceBrief key={item.id} source={item} articleSlug={article.slug} />
              ))}
              {topicBrief}
              <PartnerPlacements
                key={article.id}
                surface="article"
                article_id={article.id}
                ad_formats={["engagement"]}
              />
            </div>
          </div>
        </div>
        <footer className="preview-footer" aria-label="Article actions">
          <div className="preview-footer-content">
            <div className="preview-footer-toolbar" role="group" aria-label="Article interactions">
              <ArticleEngagement
                articleId={article.id}
                articleSlug={article.slug}
                contentFormat={article.content_format}
              />
              <ArticleBookmarkButton
                articleId={article.id}
                articleSlug={article.slug}
                contentFormat={article.content_format}
                label
              />
              <ArticleShare
                key={article.id}
                slug={article.slug}
                title={article.title}
                contentFormat={article.content_format}
                label
              />
            </div>
          </div>
        </footer>
      </article>
    </EngagementProvider>
  );
}

import { articleAction } from "@/lib/video";
import { VideoMetadata } from "./video-metadata";
import { UserDate } from "./user-date";
import Link from "@/components/reader-link";
import { ArrowUpRight, X } from "lucide-react";
import { useId, useState, type Ref } from "react";
import type { Article } from "@/lib/types";
import { displayHost, safeExternalUrl, sourceHref } from "@/lib/feed-query";

import {
  ArticleEngagement,
  ArticleBookmarkButton,
  ArticleReadLink,
  useArticleEngagement,
} from "./article-engagement";
import { ArticleImage } from "./article-image";
import { CatalogIcon } from "./catalog-icon";
import { TruncatedLink } from "./truncated-link";
import { ArticleSponsorship } from "./article-sponsorship";
import { articleDestination } from "@/lib/reader-destination";
import { adLinkRel } from "@devfeed/ui/ad-link-rel";

export function ArticleCard({
  article,
  priority = false,
  recommendation,
  promotion,
  containerRef,
  onOpen,
}: {
  article: Article;
  priority?: boolean;
  recommendation?: string;
  promotion?: { productId: string; active: boolean; visible?: boolean; preserveSpace: boolean };
  containerRef?: Ref<HTMLElement>;
  onOpen?: () => void;
}) {
  const destination = articleDestination(article.slug);
  const image = safeExternalUrl(article.image_url);
  const action = articleAction(article);
  const original = action.href;
  const source = article.sources[0];
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const engagement = useArticleEngagement(article.id);
  const sponsorshipId = useId();
  const sponsored = !!article.sponsor_names?.length;
  const promotionVisible = promotion?.visible ?? promotion?.active;
  return (
    <article
      ref={containerRef}
      className={`article-card${promotion ? " partner-placement partner-placement--content" : ""}`}
      data-product-id={promotion?.productId}
      data-ad-format={promotion ? "sponsored_content" : undefined}
      data-suspended={
        promotion && !promotionVisible && promotion.preserveSpace ? "true" : undefined
      }
      hidden={promotion && !promotionVisible && !promotion.preserveSpace}
      aria-hidden={(promotion && !promotion.active) || undefined}
      inert={promotion ? !promotion.active : undefined}
    >
      <div className="card-image">
        <ArticleImage
          src={image}
          variants={article.image_variants}
          priority={priority}
          label={article.topics[0]?.name ?? "Article"}
        />
        {original && (
          <ArticleReadLink
            articleId={article.id}
            href={original}
            target="_blank"
            rel={sponsored ? adLinkRel(article.sponsor_link_rel) : "noopener noreferrer"}
            className="article-read-link"
            aria-label={action.accessibleLabel}
            title={action.accessibleLabel}
            onClick={() => {
              if (!promotion) setFeedbackOpen(true);
            }}
          >
            {action.label}
            <ArrowUpRight size={15} aria-hidden="true" />
          </ArticleReadLink>
        )}
      </div>

      <div className="card-copy">
        <h2>
          <TruncatedLink
            href={destination}
            prefetch={false}
            className="card-open-link"
            rel={sponsored ? adLinkRel(article.sponsor_link_rel, false) || undefined : undefined}
            aria-describedby={sponsored ? sponsorshipId : undefined}
            onClick={onOpen}
          >
            {article.title}
          </TruncatedLink>
        </h2>
        <VideoMetadata content={article} />
        <div className="card-tags">
          {article.topics.slice(0, 2).map((topic) => (
            <Link href={`/topics/${encodeURIComponent(topic.slug)}`} key={topic.id}>
              #{topic.name}
            </Link>
          ))}
        </div>
        {recommendation && <p className="recommendation-reason">{recommendation}</p>}
        <div className="card-meta-row">
          <span className="card-source">
            <CatalogIcon
              className="source-avatar"
              url={source?.logo_url ?? null}
              variants={source?.logo_variants}
              displaySize={12}
              source
            />
            {source ? (
              <Link href={sourceHref(source)} title={source.name}>
                {source.name}
              </Link>
            ) : (
              <span title={displayHost(article.canonical_url)}>
                {displayHost(article.canonical_url)}
              </span>
            )}
          </span>
          <span className="content-type card-meta-type" data-content-type={article.content_type}>
            {article.content_type}
          </span>
          <ArticleSponsorship names={article.sponsor_names} id={sponsorshipId} />
        </div>
        <div className="card-bottom">
          <UserDate className="card-date" value={article.published_at ?? article.feed_at} />
          <div className="article-quick-actions">
            <ArticleEngagement
              articleId={article.id}
              articleSlug={article.slug}
              contentFormat={article.content_format}
            />
            <ArticleBookmarkButton
              articleId={article.id}
              articleSlug={article.slug}
              contentFormat={article.content_format}
            />
          </div>
        </div>
      </div>
      {feedbackOpen && !engagement?.liked && (
        <div className="article-feedback" role="dialog" aria-label="Article feedback">
          <button
            className="article-feedback-close"
            type="button"
            aria-label="Close article feedback"
            onClick={() => setFeedbackOpen(false)}
          >
            <X size={18} aria-hidden="true" />
          </button>
          <p>Want to see more {article.content_type} like this?</p>
          <ArticleEngagement
            articleId={article.id}
            articleSlug={article.slug}
            contentFormat={article.content_format}
          />
          <div className="article-feedback-card">
            <h2>{article.title}</h2>
            <ArticleImage
              src={image}
              variants={article.image_variants}
              label={article.topics[0]?.name ?? "Article"}
            />
          </div>
        </div>
      )}
    </article>
  );
}

import { useId, type ReactNode, type Ref } from "react";
import { ArrowUpRight } from "lucide-react";
import Link from "@/components/reader-link";
import type { Article } from "@/lib/types";
import { articleSource } from "@/lib/article-attribution";
import { articlePlainText } from "@/lib/article-text";
import { STACKED_IMAGE_SIZES } from "@/lib/article-images";
import { displayHost, outboundArticleUrl, safeExternalUrl, sourceHref } from "@/lib/feed-query";
import { articleDestination } from "@/lib/reader-destination";
import { adLinkRel } from "@devfeed/ui/ad-link-rel";
import { ArticleBookmarkButton, ArticleEngagement, ArticleReadLink } from "./article-engagement";
import { ArticleImage } from "./article-image";
import { ArticleShare } from "./article-share";
import { ArticleSponsorship } from "./article-sponsorship";
import { CatalogIcon } from "./catalog-icon";
import { TruncatedLink } from "./truncated-link";
import { UserDate } from "./user-date";

export function ArticleStackedFrame({ children }: { children: ReactNode }) {
  return (
    <section className="article-stacked" aria-label="Articles in stacked view">
      {children}
    </section>
  );
}

export function ArticleStackedRow({
  article,
  priority = false,
  recommendation,
  promotion,
  containerRef,
  onOpen,
  textOnly = false,
}: {
  article: Article;
  priority?: boolean;
  recommendation?: string;
  promotion?: { productId: string; active: boolean; visible?: boolean; preserveSpace: boolean };
  containerRef?: Ref<HTMLElement>;
  onOpen?: () => void;
  textOnly?: boolean;
}) {
  const destination = articleDestination(article.slug);
  const source = articleSource(article);
  const sourceName = source?.name ?? (displayHost(article.canonical_url) || "Original publisher");
  const original = outboundArticleUrl(article.canonical_url);
  const image = safeExternalUrl(article.image_url);
  const topic = article.topics[0];
  const tag = article.tags[0];
  const description = articlePlainText(article.ai_description?.trim() || article.summary);
  const titleId = useId();
  const sponsorshipId = useId();
  const sponsored = !!article.sponsor_names?.length;
  const promotionVisible = promotion?.visible ?? promotion?.active;
  const internalRel = sponsored
    ? adLinkRel(article.sponsor_link_rel, false) || undefined
    : undefined;
  const headline = (
    <h2 id={titleId}>
      <TruncatedLink
        href={destination}
        prefetch={false}
        className="article-stacked-title card-open-link"
        rel={internalRel}
        aria-describedby={sponsored ? sponsorshipId : undefined}
        onClick={onOpen}
      >
        {article.title}
      </TruncatedLink>
    </h2>
  );
  return (
    <article
      ref={containerRef}
      className={`article-stacked-row${promotion ? " partner-placement partner-placement--content" : ""}`}
      data-article-id={article.id}
      aria-labelledby={titleId}
      data-product-id={promotion?.productId}
      data-ad-format={promotion ? "sponsored_content" : undefined}
      data-suspended={
        promotion && !promotionVisible && promotion.preserveSpace ? "true" : undefined
      }
      hidden={promotion && !promotionVisible && !promotion.preserveSpace}
      aria-hidden={(promotion && !promotion.active) || undefined}
      inert={promotion ? !promotion.active : undefined}
    >
      {!textOnly && (
        <div className="article-stacked-thumbnail">
          <ArticleImage
            src={image}
            variants={article.image_variants}
            priority={priority}
            sizes={STACKED_IMAGE_SIZES}
            label={topic?.name ?? "Article"}
          />
        </div>
      )}
      <div className="article-stacked-copy">
        {textOnly && headline}
        <div className="article-stacked-meta">
          <span className="article-stacked-source">
            {!textOnly && (
              <CatalogIcon
                className="source-avatar"
                url={source?.logo_url ?? null}
                variants={source?.logo_variants}
                displaySize={12}
                source
              />
            )}
            {source ? (
              <Link href={sourceHref(source)} title={sourceName}>
                {sourceName}
              </Link>
            ) : (
              <span title={sourceName}>{sourceName}</span>
            )}
          </span>
          <UserDate value={article.published_at ?? article.feed_at} />
          {topic ? (
            <Link
              className="article-stacked-topic"
              href={`/topics/${encodeURIComponent(topic.slug)}`}
            >
              #{topic.name}
            </Link>
          ) : tag ? (
            <Link className="article-stacked-topic" href={`/?tag=${encodeURIComponent(tag)}`}>
              #{tag}
            </Link>
          ) : null}
          <ArticleSponsorship names={article.sponsor_names} id={sponsorshipId} />
        </div>
        {!textOnly && headline}
        {description && <p className="article-stacked-description">{description}</p>}
      </div>
      <div className="article-stacked-footer">
        {recommendation && <p className="recommendation-reason">{recommendation}</p>}
        <div
          className="article-stacked-actions article-quick-actions"
          role="group"
          aria-label={`Actions for ${article.title}`}
        >
          <ArticleEngagement articleId={article.id} articleSlug={article.slug} />
          {!textOnly && <ArticleBookmarkButton articleId={article.id} articleSlug={article.slug} />}
          <ArticleShare slug={article.slug} title={article.title} />
          {original && (
            <ArticleReadLink
              articleId={article.id}
              href={original}
              className="article-read-link article-stacked-read"
              target="_blank"
              rel={sponsored ? adLinkRel(article.sponsor_link_rel) : "noopener noreferrer"}
              aria-label="Read original article in a new tab"
              title="Read original article in a new tab"
            >
              <ArrowUpRight size={16} aria-hidden="true" />
            </ArticleReadLink>
          )}
          {textOnly && <ArticleBookmarkButton articleId={article.id} articleSlug={article.slug} />}
        </div>
      </div>
    </article>
  );
}

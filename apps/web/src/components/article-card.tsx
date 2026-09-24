import { UserDate } from "./user-date";
import Link from "next/link";
import { ArrowUpRight, X } from "lucide-react";
import { useState } from "react";
import type { Article } from "@/lib/types";
import { displayHost, outboundArticleUrl, safeExternalUrl, sourceHref } from "@/lib/feed-query";

import {
  ArticleEngagement,
  ArticleBookmarkButton,
  ArticleReadLink,
  useArticleEngagement,
} from "./article-engagement";
import { ArticleImage } from "./article-image";
import { CatalogIcon } from "./catalog-icon";
import { TruncatedLink } from "./truncated-link";

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
  const original = outboundArticleUrl(article.canonical_url);
  const source = article.sources[0];
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const engagement = useArticleEngagement(article.id);
  return (
    <article className="article-card">
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
            rel="noopener noreferrer"
            className="article-read-link"
            aria-label="Read original article in a new tab"
            title="Read original article in a new tab"
            onClick={() => setFeedbackOpen(true)}
          >
            Read
            <ArrowUpRight size={15} aria-hidden="true" />
          </ArticleReadLink>
        )}
      </div>

      <div className="card-copy">
        <h2>
          <TruncatedLink href={href} scroll={false} prefetch={false} className="card-open-link">
            <span>{article.title}</span>
          </TruncatedLink>
        </h2>
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
            <span className="source-avatar" aria-hidden="true">
              <CatalogIcon url={source?.logo_url ?? null} source />
            </span>
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
        </div>
        <div className="card-bottom">
          <div className="card-date">
            <UserDate value={article.published_at ?? article.feed_at} />
          </div>
          <div className="article-quick-actions">
            <ArticleEngagement articleId={article.id} articleSlug={article.slug} />
            <ArticleBookmarkButton articleId={article.id} articleSlug={article.slug} />
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
          <ArticleEngagement articleId={article.id} articleSlug={article.slug} />
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

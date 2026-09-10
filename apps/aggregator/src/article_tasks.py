"""Original article metadata enrichment using the existing guarded page transport."""

import hashlib
import logging
import time
import uuid

from devfeed_core.analysis import request_analysis
from devfeed_core.article_jobs import approved_sources, claim_article
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.editorial import invalidate_editorial
from devfeed_core.feeds.fetcher import FeedError, fetch_article_page
from devfeed_core.job_lifecycle import fail_or_retry, finish_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleTag,
    ArticleTopic,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.source_tags import attach_source_tags, resolve_source_tags
from devfeed_core.taxonomy import classify, classify_tags, detect_content_type
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import lazyload

from devfeed_aggregator.article_pages import PageArticle, extract_article

logger = logging.getLogger(__name__)


def apply_page(session, article: Article, page: PageArticle, *, source_tag_ids=()) -> list[str]:
    """Fill publisher gaps; preserve nonempty publisher-supplied fields."""
    values = {}
    if page.has_text:
        values = {
            key: value
            for key, value in {
                "title": page.title,
                "summary": page.summary,
                "author": page.author,
                "published_at": page.published_at,
                "language": page.language,
                "metadata_source_type": "page",
            }.items()
            if value is not None and value != ""
        }
        if article.metadata_source_type == "publisher":
            values = {
                key: value
                for key, value in values.items()
                if key != "metadata_source_type" and not getattr(article, key)
            }
        else:
            values["content_type"] = detect_content_type(page.title or article.title, [])
    if page.image_url and not article.image_url:
        values["image_url"] = page.image_url
    if (article.classification_provenance or {}).get("origin") in {"ai", "manual"}:
        values.pop("language", None)
        values.pop("content_type", None)
    changed = []
    for key, value in values.items():
        if getattr(article, key) != value:
            setattr(article, key, value)
            changed.append(key)
    if attach_source_tags(session, article.id, source_tag_ids):
        changed.append("tags")
    if (
        page.has_text
        and not get_settings().ai_enabled
        and (article.classification_provenance or {}).get("origin") not in {"ai", "manual"}
    ):
        # Legacy keyword classification is only a non-AI fallback. Never restore
        # guesses that a contextual analysis has already superseded.
        topics = session.scalars(select(Topic).where(Topic.status == "active")).all()
        tags = session.scalars(select(Tag)).all()
        for topic_id in sorted(classify(article.title, article.summary, [], topics)):
            session.execute(
                insert(ArticleTopic)
                .values(
                    article_id=article.id,
                    topic_id=topic_id,
                    role="supporting",
                    relevance=0.5,
                    evidence="Matched approved topic keywords",
                    origin="heuristic",
                )
                .on_conflict_do_nothing()
            )
        for tag_id in sorted(classify_tags(article.title, article.summary, [], tags)):
            session.execute(
                insert(ArticleTag)
                .values(article_id=article.id, tag_id=tag_id)
                .on_conflict_do_nothing()
            )
    return sorted(changed)


def owned_job(session, identifier, token):
    job = session.scalar(
        select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.id == identifier).with_for_update()
    )
    return job if job and job.status == "running" and job.lease_token == token else None


def enrich_article(job_id: str) -> None:
    with job_log_context("article-enrichment", job_id):
        try:
            _enrich(job_id)
        except Exception:
            logger.exception("article_enrichment_runtime_failed")
            raise


def _enrich(job_id: str) -> None:
    started = time.perf_counter()
    identifier = uuid.UUID(job_id)
    factory = session_factory()
    with factory.begin() as session:
        claimed = claim_article(session, identifier)
        if claimed is None:
            logger.debug("article_enrichment_not_claimed")
            return
        job, url = claimed
        token, article_id, attempt = job.lease_token, job.article_id, job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        _enrich_claimed(factory, identifier, token, article_id, url, started)


def _enrich_claimed(factory, identifier, token, article_id, url, started):
    logger.info("article_enrichment_started")
    try:
        # All network, DOM processing and inference happen outside a DB transaction.
        result = fetch_article_page(url)
        page = extract_article(result, utcnow())
        with factory.begin() as session:
            job = owned_job(session, identifier, token)
            if job is None:
                logger.warning("article_enrichment_lease_lost")
                return
            job.http_status, job.result = result.status, page.evidence
            # Lock source review before Article, matching ingestion's lock order.
            if not approved_sources(session, article_id, lock=True):
                finish_job(job, "unapproved", utcnow())
            else:
                # Match ingestion's Source -> Tag -> Article lock order.
                resolved, _ = resolve_source_tags(session, page.tags)
                article = session.scalar(
                    select(Article)
                    .options(lazyload("*"))
                    .where(Article.id == article_id)
                    .with_for_update(of=Article)
                )
                if article is None or article.canonical_url != url:
                    finish_job(job, "superseded", utcnow())
                else:
                    job.changed_fields = apply_page(
                        session, article, page, source_tag_ids=resolved.values()
                    )
                    if page.text:
                        digest = hashlib.sha256(page.text.encode()).hexdigest()
                        content = session.get(ArticleContent, article.id)
                        content_changed = content is None or content.content_hash != digest
                        if content is None:
                            content = ArticleContent(article_id=article.id)
                            session.add(content)
                        if content_changed:
                            content.text, content.content_hash = page.text, digest
                            content.url, content.method = (
                                result.final_url,
                                page.text_source or "page",
                            )
                            content.retrieved_at = utcnow()
                            invalidate_editorial(article)
                        elif any(field in job.changed_fields for field in ("title", "summary")):
                            invalidate_editorial(article)
                        session.flush()
                    if get_settings().ai_enabled and article.review_status != "rejected":
                        # Publisher RSS may already contain useful evidence even
                        # when a successful HTML lookup has no extractable body.
                        request_analysis(session, article.id, automatic=True)
                    finish_job(
                        job,
                        "enriched"
                        if page.has_text
                        else "metadata_only"
                        if job.changed_fields
                        else "not_found",
                        utcnow(),
                    )
            fields = {"outcome": job.outcome, "changed_fields": job.changed_fields}
        logger.info(
            "article_enrichment_completed", extra={**fields, "duration_ms": elapsed_ms(started)}
        )
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        error = (
            f"Article lookup failed: {transport.reason}"
            if transport
            else f"Article lookup error: {type(exc).__name__}"
        )
        if transport and transport.reason == "response_too_large" and transport.limit_bytes:
            error += (
                f". Page exceeds the configured {transport.limit_bytes:,}-byte article download "
                "limit (DEVFEED_ARTICLE_PAGE_MAX_BYTES)."
            )
        with factory.begin() as session:
            job = owned_job(session, identifier, token)
            if job is None:
                logger.warning("article_enrichment_lease_lost")
                return
            fail_or_retry(
                job,
                error,
                utcnow(),
                retryable=transport.retryable if transport else True,
                retry_after=transport.retry_after if transport else 0,
            )
            job.http_status = transport.status if transport else None
            if (
                job.status == "failed"
                and get_settings().ai_enabled
                and approved_sources(session, article_id, lock=True)
            ):
                article = session.scalar(
                    select(Article).where(Article.id == article_id).with_for_update(of=Article)
                )
                if article is not None and article.review_status != "rejected":
                    request_analysis(session, article_id, automatic=True)
            fields = {
                "job_status": job.status,
                "available_at": job.available_at,
                "error_type": type(exc).__name__,
                "reason": transport.reason if transport else "unexpected_error",
                "upstream_status": job.http_status,
                "duration_ms": elapsed_ms(started),
            }
        logger.log(
            logging.WARNING if transport else logging.ERROR,
            "article_enrichment_retry_scheduled"
            if fields["job_status"] == "queued"
            else "article_enrichment_failed",
            extra=fields,
            exc_info=transport is None,
        )

import logging
import time
import uuid
from datetime import timedelta

from devfeed_core.article_jobs import request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_feed
from devfeed_core.feeds.parser import ParsedFeed, parse_feed
from devfeed_core.image_jobs import request_image
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import cancel_unapproved_job, claim_job, fail_job, owned_job
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    IngestionJob,
    Source,
    Tag,
    Topic,
    utcnow,
)
from devfeed_core.source_tags import attach_source_tags, resolve_source_tags, source_tag_names
from devfeed_core.source_types import SourceType
from devfeed_core.taxonomy import classify, classify_tags, detect_content_type
from devfeed_core.urls import fingerprint
from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_aggregator.languages import detect_feed_languages

logger = logging.getLogger(__name__)


def store_entries(session: Session, source_id: uuid.UUID, parsed: ParsedFeed) -> int:
    topics = session.scalars(select(Topic).where(Topic.status == "active")).all()
    supplied_tags, _ = resolve_source_tags(
        session, (tag for entry in parsed.entries for tag in entry.tags)
    )
    tags = session.scalars(select(Tag)).all()
    created = 0
    # Stable lock order across workers importing the same URLs from different feeds.
    for entry in sorted(parsed.entries, key=lambda item: item.canonical_url):
        source_tag_ids = [supplied_tags[key] for key in source_tag_names(entry.tags)]
        existing_origin = session.scalar(
            select(ArticleOrigin.article_id).where(
                ArticleOrigin.source_id == source_id, ArticleOrigin.entry_key == entry.entry_key
            )
        )
        if existing_origin is not None:
            attach_source_tags(session, existing_origin, source_tag_ids)
            continue
        url_hash = fingerprint(entry.canonical_url)
        article_id = session.scalar(
            insert(Article)
            .values(
                canonical_url=entry.canonical_url,
                url_hash=url_hash,
                title=entry.title,
                summary=entry.summary,
                author=entry.author,
                published_at=entry.published_at,
                feed_at=entry.feed_at,
                metadata_source_type=entry.source_type,
                image_url=entry.image_url,
                language=entry.language,
                content_type=detect_content_type(entry.title, entry.tags),
            )
            .on_conflict_do_nothing(index_elements=[Article.url_hash])
            .returning(Article.id)
        )
        if article_id is not None:
            created += 1
        else:
            article_id = session.scalar(select(Article.id).where(Article.url_hash == url_hash))
            if entry.source_type == SourceType.PUBLISHER:
                # A discovery-only placeholder must not win over a publisher feed.
                # Never change identity or feed ordering as metadata improves.
                session.execute(
                    update(Article)
                    .where(
                        Article.id == article_id,
                        Article.metadata_source_type.in_([SourceType.AGGREGATOR, "page"]),
                    )
                    .values(
                        title=entry.title,
                        summary=entry.summary or Article.summary,
                        author=func.coalesce(entry.author, Article.author),
                        published_at=func.coalesce(entry.published_at, Article.published_at),
                        image_url=func.coalesce(Article.image_url, entry.image_url),
                        language=func.coalesce(entry.language, Article.language),
                        content_type=detect_content_type(entry.title, entry.tags),
                        metadata_source_type=SourceType.PUBLISHER,
                        review_status=case(
                            (Article.review_status == "rejected", "rejected"), else_="pending"
                        ),
                        publication_status="unpublished",
                        editorial_revision=Article.editorial_revision + 1,
                        ai_summary=None,
                        ai_description=None,
                        classification_provenance={},
                    )
                )
        assert article_id is not None
        attach_source_tags(session, article_id, source_tag_ids)
        session.execute(
            insert(ArticleOrigin)
            .values(
                article_id=article_id,
                source_id=source_id,
                entry_key=entry.entry_key,
                original_url=entry.original_url or entry.canonical_url,
                source_metadata=entry.source_metadata,
            )
            .on_conflict_do_nothing(
                index_elements=[ArticleOrigin.source_id, ArticleOrigin.entry_key]
            )
        )
        if (
            entry.source_type == SourceType.AGGREGATOR
            or not entry.summary
            or get_settings().ai_enabled
        ):
            # The page lookup also discovers an image, avoiding a second HTTP job.
            request_article_enrichment(session, article_id, automatic=True)
        else:
            request_image(session, article_id, automatic=True)
        if get_settings().ai_enabled:
            continue
        if session.scalar(
            select(Article.classification_provenance["origin"].as_string()).where(
                Article.id == article_id
            )
        ) in {"ai", "manual"}:
            continue
        for topic_id in sorted(classify(entry.title, entry.summary, entry.tags, topics)):
            session.execute(
                insert(ArticleTopic)
                .values(
                    article_id=article_id,
                    topic_id=topic_id,
                    role="supporting",
                    relevance=0.5,
                    evidence="Matched approved topic keywords",
                    origin="heuristic",
                )
                .on_conflict_do_nothing()
            )
        for tag_id in sorted(classify_tags(entry.title, entry.summary, entry.tags, tags)):
            session.execute(
                insert(ArticleTag)
                .values(article_id=article_id, tag_id=tag_id)
                .on_conflict_do_nothing()
            )
    return created


def ingest(job_id: str) -> None:
    with job_log_context("ingestion", job_id):
        try:
            _ingest(job_id)
        except Exception:
            # Includes claim/commit failures and failures to persist a retry decision.
            logger.exception("ingestion_runtime_failed")
            raise


def _ingest(job_id: str) -> None:
    started = time.perf_counter()
    identifier = uuid.UUID(job_id)
    factory = session_factory()
    with factory.begin() as session:
        claimed = claim_job(session, identifier)
        if not claimed:
            logger.debug("ingestion_not_claimed")
            return
        claimed_job, claimed_source = claimed
        lease_token = claimed_job.lease_token
        url, etag, modified = (
            claimed_source.feed_url,
            claimed_source.etag,
            claimed_source.last_modified,
        )
        source_id, attempt = claimed_source.id, claimed_job.attempts
        source_type = SourceType(claimed_source.source_type)
    with log_context(source_id=source_id, source_type=source_type, attempt=attempt):
        _ingest_claimed(factory, identifier, lease_token, url, etag, modified, started, source_type)


def _ingest_claimed(factory, identifier, lease_token, url, etag, modified, started, source_type):
    logger.info("ingestion_started")
    # No database transaction or connection is held while downloading/parsing.
    try:
        result = fetch_feed(url, etag, modified)
        parsed = (
            detect_feed_languages(
                parse_feed(result.body, result.final_url, utcnow(), source_type=source_type)
            )
            if result.status == 200
            else None
        )
        with factory.begin() as session:
            job = owned_job(session, IngestionJob, identifier, lease_token)
            if job is None:
                logger.warning("ingestion_lease_lost")
                return  # A recovered job has a newer owner; this worker cannot commit.
            source = session.scalar(
                select(Source).where(Source.id == job.source_id).with_for_update()
            )
            assert source is not None
            if source.approval_status != "approved":
                cancel_unapproved_job(job)
                logger.info("ingestion_source_unapproved")
                return
            if not source.enabled:
                fail_job(session, job, "Source was disabled during ingestion", retryable=False)
                logger.info("ingestion_source_disabled")
                return
            if parsed:
                job.articles_created = store_entries(session, source.id, parsed)
                job.entries_seen = parsed.seen
                job.entries_skipped = parsed.skipped
            job.status = "succeeded"
            job.http_status = result.status
            job.finished_at = utcnow()
            job.lease_token = None
            job.lease_until = None
            job.error = None
            source.last_success_at = job.finished_at
            source.last_error = None
            source.consecutive_failures = 0
            source.next_fetch_at = job.finished_at + timedelta(seconds=source.poll_interval_seconds)
            # A 200 without validators must clear old validators. A 304 may omit them.
            source.etag = result.etag if result.status == 200 else result.etag or source.etag
            source.last_modified = (
                result.last_modified
                if result.status == 200
                else result.last_modified or source.last_modified
            )
            metrics = {
                "articles_created": job.articles_created,
                "entries_seen": job.entries_seen,
                "entries_skipped": job.entries_skipped,
                "upstream_status": result.status,
            }
        logger.info("ingestion_succeeded", extra={**metrics, "duration_ms": elapsed_ms(started)})
    except Exception as exc:
        # Keep URLs, feed contents, credentials and database parameters out of job errors/logs.
        feed_error = exc if isinstance(exc, FeedError) else None
        error = str(feed_error) if feed_error else f"Ingestion error: {type(exc).__name__}"
        logger.log(
            logging.WARNING if feed_error else logging.ERROR,
            "ingestion_attempt_failed",
            extra={
                "error_type": type(exc).__name__,
                "reason": feed_error.reason if feed_error else "unexpected_error",
                "upstream_status": feed_error.status if feed_error else None,
                "retryable": feed_error.retryable if feed_error else True,
                "duration_ms": elapsed_ms(started),
            },
            exc_info=not bool(feed_error),
        )
        outcome = None
        with factory.begin() as session:
            job = owned_job(session, IngestionJob, identifier, lease_token)
            if job is not None:
                fail_job(
                    session,
                    job,
                    error,
                    retryable=feed_error.retryable if feed_error else True,
                    retry_after=feed_error.retry_after if feed_error else 0,
                )
                job.http_status = feed_error.status if feed_error else None
                outcome = {"job_status": job.status, "available_at": job.available_at}
        if outcome:
            logger.warning(
                "ingestion_retry_scheduled"
                if outcome["job_status"] == "queued"
                else "ingestion_failed",
                extra=outcome,
            )
        else:
            logger.warning("ingestion_lease_lost")

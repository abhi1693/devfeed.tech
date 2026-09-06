"""Independent RQ image discovery with durable retries and ownership-checked writes."""

import logging
import time
import uuid

from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_page
from devfeed_core.image_jobs import claim_image, fail_image, finish_image
from devfeed_core.images import extract_image
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.models import Article, ArticleImageJob
from sqlalchemy import select, update

logger = logging.getLogger(__name__)


def enrich_image(job_id: str) -> None:
    with log_context(service="worker", job_id=job_id):
        try:
            _enrich(job_id)
        except Exception:
            logger.exception("image_runtime_failed")
            raise


def _enrich(job_id: str) -> None:
    started = time.perf_counter()
    identifier = uuid.UUID(job_id)
    factory = session_factory()
    with factory.begin() as session:
        claimed = claim_image(session, identifier)
        if claimed is None:
            logger.debug("image_not_claimed")
            return
        job, url = claimed
        token, article_id, attempt = job.lease_token, job.article_id, job.attempts
    with log_context(article_id=article_id, attempt=attempt):
        _enrich_claimed(factory, identifier, token, article_id, url, started)


def _enrich_claimed(factory, identifier, token, article_id, url, started):
    logger.info("image_lookup_started")
    try:
        # No database connection/transaction held during HTTP or parsing.
        result = fetch_page(url)
        image = extract_image(result)
        with factory.begin() as session:
            job = owned_job(session, identifier, token)
            if job is None:
                logger.warning("image_lease_lost")
                return
            outcome = "not_found"
            if image:
                changed = session.scalar(
                    update(Article)
                    .where(Article.id == article_id, Article.image_url.is_(None))
                    .values(image_url=image.url)
                    .returning(Article.id)
                )
                outcome = "found" if changed is not None else "already_present"
                job.image_url, job.method = image.url, image.method
            job.http_status = result.status
            finish_image(job, outcome)
        logger.info(
            "image_lookup_completed",
            extra={
                "outcome": outcome,
                "image_method": image.method if image else None,
                "duration_ms": elapsed_ms(started),
            },
        )
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        # Never store URL, page body, raw exception message, SQL or request parameters.
        error = (
            f"Image lookup failed: {transport.reason}"
            if transport
            else f"Image lookup error: {type(exc).__name__}"
        )
        with factory.begin() as session:
            job = owned_job(session, identifier, token)
            if job is None:
                logger.warning("image_lease_lost")
                return
            fail_image(
                job,
                error,
                retryable=transport.retryable if transport else True,
                retry_after=transport.retry_after if transport else 0,
            )
            job.http_status = transport.status if transport else None
            fields = {
                "job_status": job.status,
                "available_at": job.available_at,
                "error_type": type(exc).__name__,
                "reason": transport.reason if transport else "unexpected_error",
                "upstream_status": transport.status if transport else None,
                "duration_ms": elapsed_ms(started),
            }
        logger.log(
            logging.WARNING if transport else logging.ERROR,
            "image_retry_scheduled" if fields["job_status"] == "queued" else "image_lookup_failed",
            extra=fields,
            exc_info=transport is None,
        )


def owned_job(session, identifier, token):
    job = session.scalar(
        select(ArticleImageJob).where(ArticleImageJob.id == identifier).with_for_update()
    )
    return job if job and job.status == "running" and job.lease_token == token else None

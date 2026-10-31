"""Independent RQ image discovery with durable retries and ownership-checked writes."""

import logging
import time
import uuid

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, fetch_page
from devfeed_core.image_jobs import claim_image, current_refresh_image, request_image
from devfeed_core.images import PageImage, extract_image
from devfeed_core.job_lifecycle import fail_or_retry, finish_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import owned_job
from devfeed_core.logging import elapsed_ms, log_context
from devfeed_core.logos import logo_job_target
from devfeed_core.models import Article, ArticleImageJob, utcnow
from sqlalchemy import select, update
from sqlalchemy.orm import Session, lazyload, sessionmaker

from devfeed_aggregator.solver_jobs import defer_to_solver

logger = logging.getLogger(__name__)


def enrich_image(job_id: str) -> None:
    with job_log_context("images", job_id):
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
        refresh_id = job.refresh_id
        operation = job.operation
        product_id = getattr(job, "product_id", None)
        account_id = getattr(job, "account_id", None)
        cleanup_upload = bool((getattr(job, "storage", None) or {}).get("cleanup_upload"))
        target = logo_job_target(job)
        subject_id = getattr(job, target.job_field) if target else None
    assert token is not None  # claim_image initialized ownership.
    with log_context(article_id=article_id, attempt=attempt):
        if operation == "product-ad-image":
            assert account_id is not None and product_id is not None
            from devfeed_aggregator.image_storage_tasks import store_product_ad_image

            if cleanup_upload:
                from devfeed_aggregator.image_storage_tasks import cleanup_product_ad_image

                cleanup_product_ad_image(factory, identifier, token, account_id, product_id)
            else:
                store_product_ad_image(factory, identifier, token, account_id, product_id, url)
            return
        if operation == "product-image":
            assert product_id is not None
            from devfeed_aggregator.image_storage_tasks import store_product_image

            store_product_image(factory, identifier, token, product_id, url)
            return
        if target:
            assert subject_id is not None
            from devfeed_aggregator.image_storage_tasks import store_logo

            store_logo(factory, identifier, token, subject_id, url, model=target.model)
            return
        if operation == "store":
            assert article_id is not None
            from devfeed_aggregator.image_storage_tasks import store_image

            store_image(factory, identifier, token, article_id, url)
            return
        assert article_id is not None
        _enrich_claimed(
            factory,
            identifier,
            token,
            article_id,
            url,
            started,
            **({"refresh_id": refresh_id} if refresh_id is not None else {}),
        )


def _finish_refresh_discovery(
    session: Session, article: Article, job: ArticleImageJob, url: str, image: PageImage | None
) -> uuid.UUID | None:
    from devfeed_core.article_refresh import touch_refresh

    candidate = current_refresh_image(session, article, job)
    if candidate is None or article.canonical_url != url:
        finish_job(job, "already_present", utcnow())
        return None
    outcome = "not_found"
    if image is not None:
        expected = (
            (job.storage or {}).get("previous_image_url")
            if (job.storage or {}).get("article_refresh")
            else None
        )
        outcome = "already_present"
        if candidate.image_url == expected:
            candidate.image_url, candidate.managed_image = image.url, {}
            outcome = "found"
        job.image_url, job.method = image.url, image.method
    touch_refresh(candidate)
    finish_job(job, outcome, utcnow())
    if get_settings().image_storage_enabled:
        session.flush()
        request_image(
            session,
            article.id,
            automatic=True,
            refresh=outcome == "found",
            refresh_id=candidate.id,
        )
    return candidate.id


def _enrich_claimed(
    factory: sessionmaker[Session],
    identifier: uuid.UUID,
    token: uuid.UUID,
    article_id: uuid.UUID,
    url: str,
    started: float,
    *,
    refresh_id: uuid.UUID | None = None,
) -> None:
    logger.info("image_lookup_started")
    try:
        # No database connection/transaction held during HTTP or parsing.
        result = fetch_page(url)
        image = extract_image(result, allow_article_image=True)
        promote = None
        with factory.begin() as session:
            # Match claims and storage completion: article before image job.
            article = None
            if refresh_id is not None:
                from devfeed_core.article_jobs import approved_sources

                approved_sources(session, article_id, lock=True)
                article = session.scalar(
                    select(Article)
                    .options(lazyload("*"))
                    .where(Article.id == article_id)
                    .with_for_update()
                )
            else:
                session.scalar(select(Article.id).where(Article.id == article_id).with_for_update())
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None:
                logger.warning("image_lease_lost")
                return
            if job.refresh_id != refresh_id:
                return
            job.http_status = result.status
            if refresh_id is not None:
                if article is None:
                    finish_job(job, "already_present", utcnow())
                else:
                    promote = _finish_refresh_discovery(session, article, job, url, image)
                outcome = job.outcome
            else:
                outcome = _finish_article_discovery(session, job, article_id, url, image)
        logger.info(
            "image_lookup_completed",
            extra={
                "outcome": outcome,
                "image_method": image.method if image else None,
                "duration_ms": elapsed_ms(started),
            },
        )
        if promote is not None:
            from devfeed_core.article_refresh import try_promote_refresh

            try_promote_refresh(factory, promote)
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        # Never store URL, page body, raw exception message, SQL or request parameters.
        error = (
            f"Image lookup failed: {transport.reason}"
            if transport
            else f"Image lookup error: {type(exc).__name__}"
        )
        with factory.begin() as session:
            article = None
            if refresh_id is not None:
                from devfeed_core.article_jobs import approved_sources

                approved_sources(session, article_id, lock=True)
                article = session.scalar(
                    select(Article)
                    .options(lazyload("*"))
                    .where(Article.id == article_id)
                    .with_for_update()
                )
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None:
                logger.warning("image_lease_lost")
                return
            if job.refresh_id != refresh_id:
                return
            if refresh_id is not None and (
                article is None or current_refresh_image(session, article, job) is None
            ):
                finish_job(job, "already_present", utcnow())
                return
            if not defer_to_solver(job, transport, error):
                fail_or_retry(
                    job,
                    error,
                    utcnow(),
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


def _finish_article_discovery(
    session: Session, job: ArticleImageJob, article_id: uuid.UUID, url: str, image: PageImage | None
) -> str:
    outcome = "not_found"
    if image:
        changed = session.scalar(
            update(Article)
            .where(
                Article.id == article_id,
                Article.canonical_url == url,
                Article.publication_status != "published",
                Article.image_url.is_not_distinct_from(
                    (job.storage or {}).get("previous_image_url")
                )
                if (job.storage or {}).get("article_refresh")
                else Article.image_url.is_(None),
            )
            .values(image_url=image.url)
            .returning(Article.id)
        )
        outcome = "found" if changed is not None else "already_present"
        job.image_url, job.method = image.url, image.method
    finish_job(job, outcome, utcnow())
    if get_settings().image_storage_enabled:
        session.flush()
        request_image(session, article_id, automatic=True)
    return outcome

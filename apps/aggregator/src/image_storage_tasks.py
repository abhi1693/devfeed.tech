"""Storage stage of existing image jobs; persist progress between network operations."""

import logging
from contextlib import closing

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import fail_or_retry, finish_job
from devfeed_core.jobs import owned_job
from devfeed_core.models import Article, ArticleImageJob, utcnow
from sqlalchemy import select, update

from devfeed_aggregator.image_storage import (
    storage_client,
    store_original,
    store_variant,
    variant_widths,
)

logger = logging.getLogger(__name__)


def checkpoint(factory, identifier, token, asset) -> bool:
    with factory.begin() as session:
        job = owned_job(session, ArticleImageJob, identifier, token)
        if job is None:
            return False
        job.storage = dict(asset)
    return True


def store_image(factory, identifier, token, article_id, source):
    try:
        with factory() as session:
            job = session.get(ArticleImageJob, identifier)
            asset = dict(job.storage or {})
        with closing(storage_client()) as client:
            if not asset:
                asset = store_original(client, source)
                if not checkpoint(factory, identifier, token, asset):
                    return
            for width in variant_widths(asset):
                if any(item["width"] == width for item in asset["variants"]):
                    continue
                variant = store_variant(client, asset, width)
                asset = {**asset, "variants": [*asset["variants"], variant]}
                if not checkpoint(factory, identifier, token, asset):
                    return
        with factory.begin() as session:
            # Match the article-before-job order used when scheduling new image work.
            session.execute(select(Article.id).where(Article.id == article_id).with_for_update())
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None:
                return
            changed = session.scalar(
                update(Article)
                .where(Article.id == article_id, Article.image_url == source)
                .values(managed_image=asset)
                .returning(Article.id)
            )
            job.method = "r2-imgproxy"
            finish_job(job, "found" if changed else "already_present", utcnow())
        logger.info("image_storage_completed", extra={"variant_count": len(asset["variants"])})
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        # Exceptions from S3/proxy clients can contain signed URLs: persist only safe categories.
        with factory.begin() as session:
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is not None:
                reason = transport.reason if transport else type(exc).__name__
                fail_or_retry(
                    job,
                    f"Image storage failed: {reason}",
                    utcnow(),
                    retryable=transport.retryable if transport else True,
                    retry_after=transport.retry_after if transport else 0,
                )
        logger.warning("image_storage_failed", extra={"error_type": type(exc).__name__})

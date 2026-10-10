"""Checkpointed image storage shared by article photos and catalog logos."""

import logging
from contextlib import closing

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, finish_job
from devfeed_core.jobs import owned_job
from devfeed_core.models import Article, ArticleImageJob, Source, Topic, utcnow
from sqlalchemy import select

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


def publish_logo_original(
    factory, identifier, token, subject_id, source, asset, *, defer=False, model
):
    """Expose a saved original while variants finish, preserving completed replacements."""
    with factory.begin() as session:
        subject = session.scalar(select(model).where(model.id == subject_id).with_for_update())
        job = owned_job(session, ArticleImageJob, identifier, token)
        if job is None or subject is None:
            return False
        if subject.logo_url == source and not (subject.managed_logo or {}).get("variants"):
            subject.managed_logo = {**asset, "variants": []}
        if defer:
            job.status = "queued"
            job.available_at = utcnow()
            job.dispatched_at = None
            job.attempts = 0  # Variant generation has its own retry budget.
            job.error = None
            clear_lease(job)
    return True


def store_image(factory, identifier, token, article_id, source):
    _store_asset(
        factory,
        identifier,
        token,
        article_id,
        source,
        model=Article,
        source_field="image_url",
        asset_field="managed_image",
        original=store_original,
        variant=store_variant,
        sizes=variant_widths,
        method="r2-imgproxy",
    )


def store_logo(factory, identifier, token, subject_id, source, *, model):
    from devfeed_core.logos import logo_sizes

    from devfeed_aggregator.logo_storage import store_logo_original, store_logo_variant

    _store_asset(
        factory,
        identifier,
        token,
        subject_id,
        source,
        model=model,
        source_field="logo_url",
        asset_field="managed_logo",
        original=store_logo_original,
        variant=store_logo_variant,
        sizes=lambda asset: logo_sizes(model),
        method="r2-logo",
    )


def _replacement(session, model, subject):
    if model in {Topic, Source}:
        from devfeed_core.logos import request_logo

        session.flush()
        request_logo(session, model, subject.id, automatic=True)


def _store_asset(
    factory,
    identifier,
    token,
    subject_id,
    source,
    *,
    model,
    source_field,
    asset_field,
    original,
    variant,
    sizes,
    method,
):
    try:
        with factory() as session:
            job = session.get(ArticleImageJob, identifier)
            if job is None or job.status != "running" or job.lease_token != token:
                return
            asset = dict(job.storage or {})
        with closing(storage_client()) as client:
            imported = not asset
            if imported:
                asset = original(client, source)
                if not checkpoint(factory, identifier, token, asset):
                    return
            if model in {Topic, Source} and not publish_logo_original(
                factory, identifier, token, subject_id, source, asset, defer=imported, model=model
            ):
                return
            if model in {Topic, Source} and imported:
                return  # Resume from R2 in a later delivery, after older original imports.
            for size in sizes(asset):
                if any(item["width"] == size for item in asset["variants"]):
                    continue
                item = variant(client, asset, size)
                asset = {**asset, "variants": [*asset["variants"], item]}
                if not checkpoint(factory, identifier, token, asset):
                    return
        with factory.begin() as session:
            subject = session.scalar(select(model).where(model.id == subject_id).with_for_update())
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None or subject is None:
                return
            current = getattr(subject, source_field) == source
            if current:
                setattr(subject, asset_field, asset)
            job.method = method
            finish_job(job, "found" if current else "already_present", utcnow())
            if not current:
                _replacement(session, model, subject)
        logger.info("image_storage_completed", extra={"variant_count": len(asset["variants"])})
    except Exception as exc:
        transport = exc if isinstance(exc, FeedError) else None
        # Client exceptions may contain signed URLs; persist safe categories only.
        reason = transport.reason if transport else type(exc).__name__
        with factory.begin() as session:
            subject = session.scalar(select(model).where(model.id == subject_id).with_for_update())
            job = owned_job(session, ArticleImageJob, identifier, token)
            if job is None or subject is None:
                return
            if model in {Topic, Source} and getattr(subject, source_field) != source:
                finish_job(job, "already_present", utcnow())
                _replacement(session, model, subject)
                return
            job.http_status = transport.status if transport else None
            fail_or_retry(
                job,
                f"Image storage failed: {reason}",
                utcnow(),
                retryable=transport.retryable if transport else True,
                retry_after=transport.retry_after if transport else 0,
            )
        logger.warning("image_storage_failed", extra={"reason": reason})

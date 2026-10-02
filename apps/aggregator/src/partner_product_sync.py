"""One product per job: external I/O outside transactions, atomic catalog writes."""

import logging
import uuid

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, start_job
from devfeed_core.models import PartnerConnection, PartnerPipelineJob, PartnerProduct, utcnow
from devfeed_core.partner_catalog import lock_catalog, record_verified_redirect
from devfeed_core.partner_connections import (
    ProductCandidate,
    read_partner_product,
    upsert_partner_product,
)
from devfeed_core.research_evidence import fetched_page
from sqlalchemy import select

logger = logging.getLogger(__name__)


def process_product_sync(identifier, factory, *, product_reader=None, page_fetcher=None):
    from devfeed_aggregator.partner_sync_tasks import job_fields, pending_redirect_proofs, runnable

    with factory.begin() as session:
        lock_catalog(session)
        job = session.scalar(
            select(PartnerPipelineJob)
            .where(PartnerPipelineJob.id == identifier, runnable(utcnow()))
            .with_for_update()
        )
        if not job:
            return
        connection = session.get(PartnerConnection, job.provider)
        parent = session.get(PartnerPipelineJob, job.parent_id)
        if (
            not connection
            or not connection.enabled
            or not parent
            or parent.status not in {"queued", "running", "failed"}
            or connection.sync_revision != parent.payload["connection_revision"]
        ):
            fail_or_retry(job, "Connection or sync generation changed", utcnow(), retryable=False)
            return
        if job.attempts >= 3:
            fail_or_retry(job, "Product sync exhausted its retry limit", utcnow(), retryable=False)
            return
        token = start_job(job, utcnow(), 120)
        payload, provider = job.payload, job.provider
        revision, parent_id = connection.sync_revision, parent.id
        generation = uuid.UUID(parent.payload["generation"])
        fields = job_fields(job)
    logger.info("partner_pipeline_started", extra=fields)
    retryable, retry_after, error_fields = True, 0, {}
    try:
        candidate = ProductCandidate.model_validate(payload["candidate"])
        item = (product_reader or read_partner_product)(provider, candidate)
        if item.provider != provider or item.external_id != candidate.external_id:
            raise ValueError("Partner product identity changed")
        proofs = pending_redirect_proofs(factory, [item], page_fetcher or fetched_page)
        with factory.begin() as session:
            lock_catalog(session)
            job = session.get(PartnerPipelineJob, identifier)
            connection = session.get(PartnerConnection, provider)
            parent = session.get(PartnerPipelineJob, parent_id)
            if not job or job.status != "running" or job.lease_token != token:
                logger.info("partner_pipeline_result_discarded", extra=fields)
                return
            if (
                not connection
                or not connection.enabled
                or connection.sync_revision != revision
                or not parent
                or parent.status not in {"queued", "running", "failed"}
            ):
                fail_or_retry(
                    job, "Connection or sync generation changed", utcnow(), retryable=False
                )
                return
            for product_id, product_revision, final_url in proofs:
                product = session.get(PartnerProduct, product_id)
                if product and product.revision == product_revision and not product.merged_into_id:
                    record_verified_redirect(session, product, final_url)
            product = upsert_partner_product(session, item, generation)
            session.flush()
            job.product_id = product.id
            job.status, job.error, job.finished_at = "succeeded", None, utcnow()
            clear_lease(job)
            product_id = str(product.id)
        logger.info("partner_product_sync_completed", extra={**fields, "product_id": product_id})
        return
    except FeedError as exc:
        error = "Product API could not be read" + (f" (HTTP {exc.status})" if exc.status else "")
        retryable, retry_after = exc.retryable, exc.retry_after or 0
        error_fields = {"http_status": exc.status, "error_type": type(exc).__name__}
    except Exception as exc:
        error = "Product sync failed; completed products have been kept"
        retryable = not isinstance(exc, ValueError)
        error_fields = {"error_type": type(exc).__name__}
    # Catalog failures roll back only this product; record its retry in a fresh transaction.
    with factory.begin() as session:
        lock_catalog(session)
        job = session.get(PartnerPipelineJob, identifier)
        if not job or job.status != "running" or job.lease_token != token:
            return
        fail_or_retry(job, error, utcnow(), retryable=retryable, retry_after=retry_after)
        fields.update(
            status=job.status,
            retry_at=job.available_at.isoformat() if job.status == "queued" else None,
        )
    logger.warning(
        "partner_pipeline_retry_scheduled"
        if fields["status"] == "queued"
        else "partner_pipeline_failed",
        extra={**fields, **error_fields, "error": error},
    )

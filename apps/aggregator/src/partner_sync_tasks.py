"""Durable API sync and automatic qualification for launch platform partnerships."""

import logging
import time
import uuid
from datetime import timedelta

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.logging import elapsed_ms
from devfeed_core.models import (
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductURL,
    utcnow,
)
from devfeed_core.partner_catalog import (
    active_provider,
    has_active_listing,
    lock_catalog,
    record_verified_redirect,
    withdraw_missing_listings,
)
from devfeed_core.partner_connections import (
    ProductCandidate,
    Qualification,
    qualification_prompt,
    queue_evaluation,
    read_partner_page,
    request_assessment,
    request_sync,
    validate_qualification,
)
from devfeed_core.partner_connectors import connector_snapshot
from devfeed_core.partner_tools import ProductInput, product_snapshot
from devfeed_core.research_evidence import fetched_page
from devfeed_core.urls import canonicalize_url, fingerprint
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)


def job_fields(job):
    return {
        "job_kind": "partner-pipeline",
        "job_id": str(job.id),
        "provider": job.provider,
        "operation": job.operation,
        "product_id": str(job.product_id) if job.product_id else None,
        "attempt": job.attempts,
        "parent_id": str(job.parent_id) if job.parent_id else None,
        "external_id": job.external_id,
        "page": job.payload.get("pages", 0) + 1,
    }


def qualify(product, page):
    from devfeed_aggregator.codex_client import CodexClient

    client = CodexClient(get_settings())
    client.operation = "partner_qualification"
    return client.complete(qualification_prompt(product, page), Qualification.model_json_schema())


def runnable(now):
    return or_(
        (PartnerPipelineJob.status == "queued") & (PartnerPipelineJob.available_at <= now),
        (PartnerPipelineJob.status == "running") & (PartnerPipelineJob.lease_until < now),
    )


def pending_redirect_proofs(factory, items, fetcher):
    candidates = []
    with factory() as session:
        for item in items:
            target = canonicalize_url(item.product_url)
            listing = session.scalar(
                select(PartnerListing).where(
                    PartnerListing.provider == item.provider,
                    PartnerListing.external_id == item.external_id,
                )
            )
            if not listing:
                continue
            alias = session.get(PartnerProductURL, fingerprint(target))
            if alias and alias.product_id == listing.product_id:
                continue
            product = session.get(PartnerProduct, listing.product_id)
            if product and not product.merged_into_id:
                candidates.append((product.id, product.revision, product.product_url, target))
    proofs = []
    for identifier, revision, source, target in candidates[:10]:
        try:
            page = fetcher(source, 5)
            if canonicalize_url(page["final_url"]) == target:
                proofs.append((identifier, revision, page["final_url"]))
        except Exception:
            logger.info(
                "partner_listing_identity_unresolved", extra={"product_id": str(identifier)}
            )
    return proofs


def process_pipeline(
    job_id: str, *, factory=None, reader=None, page_fetcher=None, assessor=None, product_reader=None
):
    with job_log_context("partner-pipeline", job_id):
        try:
            factory = factory or session_factory()
            with factory() as session:
                operation = session.scalar(
                    select(PartnerPipelineJob.operation).where(
                        PartnerPipelineJob.id == uuid.UUID(job_id)
                    )
                )
            if operation == "sync_product":
                from devfeed_aggregator.partner_product_sync import process_product_sync

                return process_product_sync(
                    uuid.UUID(job_id),
                    factory,
                    product_reader=product_reader,
                    page_fetcher=page_fetcher,
                )
            return _process_pipeline(
                job_id, factory=factory, reader=reader, page_fetcher=page_fetcher, assessor=assessor
            )
        except Exception as exc:
            logger.error(
                "partner_pipeline_runtime_failed", extra={"error_type": type(exc).__name__}
            )
            raise


def finish_discovery(session, job, connection):
    """Finalize only after all individual writes succeed; cleanup is bounded per transaction."""
    states = dict(
        session.execute(
            select(PartnerPipelineJob.status, func.count())
            .where(
                PartnerPipelineJob.parent_id == job.id,
            )
            .group_by(PartnerPipelineJob.status)
        ).all()
    )
    if states.get("queued", 0) or states.get("running", 0):
        job.status, job.dispatched_at, job.attempts = "queued", None, 0
        job.available_at = utcnow() + timedelta(seconds=5)
        clear_lease(job)
        return
    if states.get("failed", 0):
        error = (
            f"{states['failed']} product syncs failed; retry sync to resume only failed products"
        )
        fail_or_retry(job, error, utcnow(), retryable=False)
        connection.last_error = error
        return
    withdrawn = withdraw_missing_listings(
        session, job.provider, uuid.UUID(job.payload["generation"]), limit=25
    )
    job.payload = {
        **job.payload,
        "withdrawn": job.payload.get("withdrawn", 0) + withdrawn,
        "finalize_batches": job.payload.get("finalize_batches", 0) + 1,
    }
    if withdrawn == 25:
        job.status, job.dispatched_at, job.attempts = "queued", None, 0
        job.available_at = utcnow()
        clear_lease(job)
        return
    connection.last_sync_at, connection.last_error = utcnow(), None
    connection.next_sync_at = utcnow() + timedelta(minutes=connection.sync_interval_minutes)
    job.status, job.error, job.finished_at = "succeeded", None, utcnow()
    clear_lease(job)
    logger.info(
        "partner_sync_completed",
        extra={
            **job_fields(job),
            "pages_processed": job.payload["pages"],
            "products_synced": states.get("succeeded", 0),
            "listings_withdrawn": job.payload["withdrawn"],
            "next_sync_at": connection.next_sync_at.isoformat(),
        },
    )


def _process_pipeline(job_id: str, *, factory=None, reader=None, page_fetcher=None, assessor=None):
    started = time.perf_counter()
    factory = factory or session_factory()
    identifier = uuid.UUID(job_id)
    with factory.begin() as session:
        lock_catalog(session)
        provider = session.scalar(
            select(PartnerPipelineJob.provider).where(PartnerPipelineJob.id == identifier)
        )
        if not provider:
            logger.debug("partner_pipeline_not_found")
            return
        connection = session.scalar(
            select(PartnerConnection)
            .where(PartnerConnection.provider == provider)
            .with_for_update()
        )
        job = session.scalar(
            select(PartnerPipelineJob)
            .where(PartnerPipelineJob.id == identifier, runnable(utcnow()))
            .with_for_update()
        )
        if not job:
            logger.debug("partner_pipeline_not_claimed", extra={"provider": provider})
            return
        available = (
            bool(connection and connection.enabled)
            if job.operation == "sync"
            else bool(active_provider(session, job.product_id))
        )
        if not available:
            logger.info(
                "partner_pipeline_cancelled",
                extra={**job_fields(job), "reason": "connection_paused"},
            )
            fail_or_retry(job, "Connection paused", utcnow(), retryable=False)
            return
        if job.operation == "sync" and job.payload.get("discovery_complete"):
            if connection.sync_revision != job.payload["connection_revision"]:
                fail_or_retry(job, "Connection changed", utcnow(), retryable=False)
                return
            finish_discovery(session, job, connection)
            return
        if job.attempts >= 3:
            logger.warning("partner_pipeline_retry_exhausted", extra=job_fields(job))
            exhausted_reason = "Automatic checks exhausted their retry limit"
            fail_or_retry(job, exhausted_reason, utcnow(), retryable=False)
            if job.operation == "sync":
                connection.last_error = exhausted_reason
            else:
                exhausted_product = session.get(PartnerProduct, job.product_id)
                if exhausted_product and exhausted_product.revision == job.payload["revision"]:
                    exhausted_product.assessment_revision = exhausted_product.revision
                    exhausted_product.assessment = {
                        "state": "attention",
                        "reason": exhausted_reason,
                    }
            return
        if job.operation == "assess" and not get_settings().ai_enabled:
            logger.info("partner_pipeline_waiting_for_ai", extra=job_fields(job))
            return
        payload, operation, connection_revision = (
            job.payload,
            job.operation,
            connection.sync_revision,
        )
        if operation == "sync" and payload["connection_revision"] != connection_revision:
            logger.info(
                "partner_pipeline_cancelled",
                extra={**job_fields(job), "reason": "connection_changed"},
            )
            fail_or_retry(job, "Connection changed", utcnow(), retryable=False)
            return
        product = session.get(PartnerProduct, job.product_id) if job.product_id else None
        if operation == "assess" and (
            not product
            or product.excluded
            or product.status == "withdrawn"
            or product.revision != payload["revision"]
        ):
            logger.info(
                "partner_pipeline_cancelled", extra={**job_fields(job), "reason": "product_changed"}
            )
            fail_or_retry(job, "Product changed", utcnow(), retryable=False)
            if product:
                request_assessment(session, product)
            return
        connector = (
            payload.get("connector") or connector_snapshot(connection)
            if operation == "sync"
            else None
        )
        snapshot = product_snapshot(product) if product else None
        token = start_job(job, utcnow(), 360)
        fields = job_fields(job)
    logger.info("partner_pipeline_started", extra=fields)
    error, retryable, retry_after = None, True, 0
    products, cursor, result = [], None, None
    error_fields = {}
    try:
        if operation == "sync":
            if connector is None:
                raise ValueError("Missing partner connector snapshot")
            products, cursor = (
                reader(payload["cursor"])
                if reader
                else read_partner_page(provider, payload["cursor"], connector)
            )
            candidates = [
                ProductCandidate(
                    external_id=item.external_id, data=item.model_dump(mode="json"), normalized=True
                )
                if isinstance(item, ProductInput)
                else ProductCandidate.model_validate(item)
                for item in products
            ]
            if cursor and (
                cursor == payload["cursor"]
                or cursor in payload["cursors"]
                or payload["pages"] >= connector["max_pages"] - 1
            ):
                raise ValueError("Partner pagination did not advance")
        else:
            if snapshot is None:
                raise ValueError("Missing partner product snapshot")
            page = (page_fetcher or fetched_page)(snapshot["product_url"], 15)
            page = {"final_url": page["final_url"], "text": page["text"][:20000]}
            # Resolve observed redirects before paying for a second assessment of the same tool.
            with factory.begin() as session:
                lock_catalog(session)
                current_job = session.get(PartnerPipelineJob, identifier)
                current_product = (
                    session.get(PartnerProduct, current_job.product_id) if current_job else None
                )
                if (
                    not current_job
                    or current_job.status != "running"
                    or current_job.lease_token != token
                ):
                    logger.warning(
                        "partner_pipeline_result_discarded",
                        extra={**fields, "reason": "lease_lost_or_cancelled"},
                    )
                    return
                if (
                    not current_product
                    or current_product.revision != payload["revision"]
                    or not active_provider(session, current_product.id)
                ):
                    fail_or_retry(
                        current_job, "Product or connection changed", utcnow(), retryable=False
                    )
                    logger.info(
                        "partner_pipeline_cancelled", extra={**fields, "reason": "input_changed"}
                    )
                    if current_product:
                        request_assessment(session, current_product)
                    return
                resolved = record_verified_redirect(session, current_product, page["final_url"])
                if resolved.id != current_product.id:
                    return
            result = validate_qualification((assessor or qualify)(snapshot, page), page)
    except FeedError as exc:
        error = (
            f"{'Platform API' if operation == 'sync' else 'Product website'} could not be read"
            + (f" (HTTP {exc.status})" if exc.status else "")
        )
        retryable, retry_after = exc.retryable, exc.retry_after or 0
        error_fields = {"http_status": exc.status, "error_type": type(exc).__name__}
    except Exception as exc:
        error_fields = {"error_type": type(exc).__name__}
        error = (
            "Platform API returned invalid product data"
            if operation == "sync"
            else "Product capabilities could not be verified"
        )
    with factory.begin() as session:
        lock_catalog(session)
        connection = session.scalar(
            select(PartnerConnection)
            .where(PartnerConnection.provider == provider)
            .with_for_update()
        )
        job = session.scalar(
            select(PartnerPipelineJob).where(PartnerPipelineJob.id == identifier).with_for_update()
        )
        if not job or job.status != "running" or job.lease_token != token:
            logger.warning(
                "partner_pipeline_result_discarded",
                extra={**fields, "reason": "lease_lost_or_cancelled"},
            )
            return
        available = (
            bool(
                connection
                and connection.enabled
                and connection.sync_revision == connection_revision
            )
            if operation == "sync"
            else bool(active_provider(session, job.product_id))
        )
        if not available:
            logger.info(
                "partner_pipeline_cancelled",
                extra={**job_fields(job), "reason": "connection_changed"},
            )
            fail_or_retry(job, "Connection changed", utcnow(), retryable=False)
            return
        product = (
            session.scalar(
                select(PartnerProduct).where(PartnerProduct.id == job.product_id).with_for_update()
            )
            if job.product_id
            else None
        )
        if operation == "assess" and (
            not product
            or product.excluded
            or product.status == "withdrawn"
            or product.revision != payload["revision"]
        ):
            logger.info(
                "partner_pipeline_cancelled", extra={**job_fields(job), "reason": "product_changed"}
            )
            fail_or_retry(job, "Product changed", utcnow(), retryable=False)
            if product:
                request_assessment(session, product)
            return
        if error:
            fail_or_retry(job, error, utcnow(), retryable=retryable, retry_after=retry_after)
            logger.warning(
                "partner_pipeline_retry_scheduled"
                if job.status == "queued"
                else "partner_pipeline_failed",
                extra={
                    **fields,
                    **error_fields,
                    "error": error,
                    "status": job.status,
                    "retry_at": job.available_at.isoformat() if job.status == "queued" else None,
                    "duration_ms": elapsed_ms(started),
                },
            )
            if operation == "sync":
                connection.last_error = error
            elif product and job.status == "failed":
                product.assessment = {"state": "attention", "reason": error}
                product.assessment_revision = product.revision
            return
        if operation == "sync":
            for candidate in candidates:
                session.execute(
                    insert(PartnerPipelineJob)
                    .values(
                        id=uuid.uuid4(),
                        provider=provider,
                        operation="sync_product",
                        parent_id=job.id,
                        external_id=candidate.external_id,
                        payload={"candidate": candidate.model_dump(mode="json")},
                    )
                    .on_conflict_do_nothing(constraint="uq_partner_product_sync")
                )
            logger.info(
                "partner_sync_page_processed",
                extra={
                    **fields,
                    "products_received": len(candidates),
                    "has_next_page": bool(cursor),
                },
            )
            job.payload = {
                **payload,
                "pipeline_version": 2,
                "cursor": cursor,
                "cursors": [*payload["cursors"], cursor] if cursor else payload["cursors"],
                "pages": payload["pages"] + 1,
                "discovery_complete": not bool(cursor),
            }
            if cursor:
                job.status, job.attempts, job.dispatched_at = "queued", 0, None
                job.available_at = utcnow() + timedelta(seconds=2)
                job.error = None
                clear_lease(job)
            else:
                finish_discovery(session, job, connection)
            return
        else:
            assert product is not None and result is not None
            if result.decision == "qualified":
                resolved_product = record_verified_redirect(session, product, page["final_url"])
                if resolved_product.id != product.id:
                    return
            product.assessment_revision = product.revision
            product.assessment = {
                "state": {
                    "qualified": "qualified",
                    "irrelevant": "irrelevant",
                    "uncertain": "attention",
                }[result.decision],
                "reason": result.reason,
            }
            product.technologies = result.technologies
            product.evidence = [item.model_dump(mode="json") for item in result.evidence]
            product.status = {
                "qualified": "approved",
                "irrelevant": "rejected",
                "uncertain": "pending",
            }[result.decision]
            product.verified_at = utcnow() if result.decision == "qualified" else None
            product.updated_at = utcnow()
            queue_evaluation(session, product)
            logger.info(
                "partner_assessment_completed",
                extra={
                    **fields,
                    "decision": result.decision,
                    "evidence_count": len(result.evidence),
                    "duration_ms": elapsed_ms(started),
                },
            )
        job.status, job.error, job.finished_at = "succeeded", None, utcnow()
        clear_lease(job)


def schedule_partner_syncs(factory):
    with factory.begin() as session:
        lock_catalog(session)
        connections = session.scalars(
            select(PartnerConnection)
            .where(PartnerConnection.enabled.is_(True))
            .with_for_update(skip_locked=True)
        ).all()
        for connection in connections:
            if connection.next_sync_at <= utcnow():
                job = request_sync(session, connection)
                created = job in session.new
                session.flush()
                if created:
                    logger.info(
                        "partner_sync_scheduled", extra={**job_fields(job), "trigger": "schedule"}
                    )
        # Qualification belongs to products, independent of whichever platform synced first.
        products = session.scalars(
            select(PartnerProduct)
            .where(
                PartnerProduct.merged_into_id.is_(None),
                has_active_listing(),
                PartnerProduct.excluded.is_(False),
                PartnerProduct.status != "withdrawn",
                PartnerProduct.assessment_revision != PartnerProduct.revision,
                ~select(PartnerPipelineJob.id)
                .where(
                    PartnerPipelineJob.product_id == PartnerProduct.id,
                    PartnerPipelineJob.status.in_(["queued", "running"]),
                    PartnerPipelineJob.operation == "assess",
                )
                .exists(),
            )
            .order_by(PartnerProduct.updated_at)
            .limit(100)
        ).all()
        for product in products:
            request_assessment(session, product)


def dispatch_partner_pipeline(factory, limit=2):
    schedule_partner_syncs(factory)
    now, dispatched = utcnow(), 0
    with factory.begin() as session:
        lock_catalog(session)
        statement = select(PartnerPipelineJob).where(
            runnable(now),
            or_(
                PartnerPipelineJob.dispatched_at.is_(None),
                PartnerPipelineJob.dispatched_at < now - timedelta(seconds=60),
            ),
        )
        if not get_settings().ai_enabled:
            statement = statement.where(PartnerPipelineJob.operation.in_(["sync", "sync_product"]))
        child = aliased(PartnerPipelineJob)
        statement = statement.where(
            ~(
                (PartnerPipelineJob.operation == "sync")
                & PartnerPipelineJob.payload["discovery_complete"].astext.is_not_distinct_from(
                    "true"
                )
                & select(child.id)
                .where(
                    child.parent_id == PartnerPipelineJob.id,
                    child.status.in_(["queued", "running"]),
                )
                .exists()
            )
        )
        jobs = session.scalars(
            statement.order_by(PartnerPipelineJob.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            # API ingestion must run with the standard background worker. Source
            # discovery has an optional dedicated worker excluded from that group.
            queue_name = "ingestion" if job.operation != "assess" else "source-analysis"
            queue = get_queue(queue_name)
            try:
                queue.enqueue(
                    "devfeed_aggregator.partner_sync_tasks.process_pipeline",
                    str(job.id),
                    # Include routing so a delivery stranded on the old optional
                    # queue cannot suppress publication to the active worker.
                    job_id=(
                        f"partner-pipeline-{queue_name}-{job.id}-{job.payload.get('pages', 0)}-"
                        f"{job.payload.get('finalize_batches', 0)}-{job.attempts}"
                    ),
                    unique=True,
                    job_timeout=300,
                    result_ttl=0,
                    failure_ttl=60,
                )
                job.dispatched_at = now
                dispatched += 1
                logger.info(
                    "partner_pipeline_dispatched",
                    extra={
                        **job_fields(job),
                        "queue": queue_name,
                    },
                )
            finally:
                queue.connection.close()
    return dispatched

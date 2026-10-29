"""Durable API sync and automatic qualification for launch platform partnerships."""

import logging
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta

from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.job_lifecycle import clear_lease, fail_or_retry, start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.json_types import JsonRecord
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
    has_active_source,
    lock_catalog,
    product_has_active_source,
    record_verified_redirect,
    withdraw_missing_listings,
)
from devfeed_core.partner_connections import (
    Qualification,
    QualificationError,
    connection_sync_interval,
    qualification_prompt,
    queue_evaluation,
    request_assessment,
    request_sync,
    validate_qualification,
)
from devfeed_core.partner_connectors import ConnectorClient, ProductCandidate, connector_snapshot
from devfeed_core.partner_credentials import connection_credential
from devfeed_core.partner_tools import QUALIFICATION_TTL, ProductInput, product_snapshot
from devfeed_core.research_evidence import FetchedPage, fetched_page
from devfeed_core.urls import canonicalize_url, fingerprint
from rq import Queue
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased, sessionmaker
from sqlalchemy.sql import ColumnElement

from devfeed_aggregator.partner_claim import claim_partner_job
from devfeed_aggregator.partner_outbox import (
    PUBLICATION_BATCH_SECONDS,
    DispatchLog,
    PartnerOutbox,
    emit_dispatch_logs,
)
from devfeed_aggregator.queue import get_publication_queue as get_queue

logger = logging.getLogger(__name__)
ASSESSMENT_RENEWAL_LEAD_TIME = timedelta(days=7)
ASSESSMENT_RENEWAL_RETRY_INTERVAL = timedelta(days=1)


@dataclass
class _PipelineTurn:
    session: Session | None = None
    assessment_first: bool = False
    published: int = 0
    pending_logs: list[DispatchLog] | None = None
    deadline: float | None = None


@contextmanager
def _committed_dispatch_logs(records: list[DispatchLog]) -> Iterator[None]:
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        if succeeded:
            emit_dispatch_logs(records)


@contextmanager
def _advance_pipeline_turn(queue: Queue, state: _PipelineTurn, key: str) -> Iterator[None]:
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        if succeeded and state.published:
            queue.connection.incr(key)


@contextmanager
def _pipeline_turn(
    factory: sessionmaker[Session], statement: Select[PartnerPipelineJob], batch_size: int
) -> Iterator[_PipelineTurn | None]:
    deadline = time.monotonic() + PUBLICATION_BATCH_SECONDS
    records: list[DispatchLog] = []
    with _committed_dispatch_logs(records), factory.begin() as session:
        if not session.scalar(select(statement.order_by(None).exists())):
            yield None
            return
        queue = get_queue("partner-pipeline")
        try:
            # Serialize turn selection and successful publication. Busy/full
            # callers cannot consume another stage's next opportunity.
            locked = session.scalar(
                select(
                    func.pg_try_advisory_xact_lock(
                        func.hashtextextended("devfeed:partner-pipeline-turn:" + queue.name, 0)
                    )
                )
            )
            if not locked or queue.count >= batch_size * 2:
                yield None
                return
            key = f"devfeed:partner-dispatch:lane-turn:{queue.name}"
            state = _PipelineTurn(
                session,
                bool(int(queue.connection.get(key) or 0) % 2),
                pending_logs=records,
                deadline=deadline,
            )
            with _advance_pipeline_turn(queue, state, key):
                yield state
        finally:
            queue.connection.close()


def job_fields(job: PartnerPipelineJob) -> dict[str, object]:
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


def qualify(product: JsonRecord, page: FetchedPage) -> JsonRecord:
    from devfeed_aggregator.codex_client import CodexClient

    client = CodexClient(get_settings())
    client.operation = "partner_qualification"
    return client.complete(qualification_prompt(product, page), Qualification.model_json_schema())


def runnable(now: datetime) -> ColumnElement[bool]:
    return or_(
        (PartnerPipelineJob.status == "queued") & (PartnerPipelineJob.available_at <= now),
        (PartnerPipelineJob.status == "running") & (PartnerPipelineJob.lease_until < now),
    )


def pending_redirect_proofs(
    factory: sessionmaker[Session],
    items: Sequence[ProductInput],
    fetcher: Callable[[str, float], FetchedPage],
) -> list[tuple[uuid.UUID, int, str]]:
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
    job_id: str,
    *,
    factory: sessionmaker[Session] | None = None,
    reader: Callable[[str | None], tuple[Sequence[ProductCandidate | ProductInput], str | None]]
    | None = None,
    page_fetcher: Callable[[str, float], FetchedPage] | None = None,
    assessor: Callable[[JsonRecord, FetchedPage], JsonRecord] | None = None,
    product_reader: Callable[[str, ProductCandidate], ProductInput] | None = None,
) -> None:
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


def finish_discovery(
    session: Session, job: PartnerPipelineJob, connection: PartnerConnection
) -> None:
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
    assert job.provider is not None  # Discovery finalization belongs to a sync connection.
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
    connection.next_sync_at = utcnow() + timedelta(
        minutes=connection_sync_interval(session, connection)
    )
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


def _process_pipeline(
    job_id: str,
    *,
    factory: sessionmaker[Session] | None = None,
    reader: Callable[[str | None], tuple[Sequence[ProductCandidate | ProductInput], str | None]]
    | None = None,
    page_fetcher: Callable[[str, float], FetchedPage] | None = None,
    assessor: Callable[[JsonRecord, FetchedPage], JsonRecord] | None = None,
) -> None:
    started = time.perf_counter()
    factory = factory or session_factory()
    identifier = uuid.UUID(job_id)

    def acquire(
        session: Session,
    ) -> tuple[PartnerPipelineJob | None, PartnerConnection | None, PartnerPipelineJob | None]:
        existing_job = session.get(PartnerPipelineJob, identifier)
        if existing_job is None:
            return None, None, None
        provider = existing_job.provider
        connection = session.scalar(
            select(PartnerConnection)
            .where(PartnerConnection.provider == provider)
            .with_for_update(nowait=True)
        )
        job = session.scalar(
            select(PartnerPipelineJob)
            .where(PartnerPipelineJob.id == identifier, runnable(utcnow()))
            .with_for_update(nowait=True)
        )
        return existing_job, connection, job

    with claim_partner_job(factory, acquire) as (session, claimed):
        existing_job, connection, job = claimed
        if existing_job is None:
            logger.debug("partner_pipeline_not_found")
            return
        provider = existing_job.provider
        if not job:
            logger.debug("partner_pipeline_not_claimed", extra={"provider": provider})
            return
        available = (
            bool(connection and connection.enabled and connection.archived_at is None)
            if job.operation == "sync"
            else product_has_active_source(session, job.product_id)
        )
        if not available:
            logger.info(
                "partner_pipeline_cancelled",
                extra={**job_fields(job), "reason": "connection_paused"},
            )
            fail_or_retry(
                job,
                "Product source is unavailable"
                if job.operation == "assess"
                else "Connection paused",
                utcnow(),
                retryable=False,
            )
            return
        if job.operation == "sync":
            assert connection is not None  # The available check above admits a live connection.
        if job.operation == "sync" and job.payload.get("discovery_complete"):
            assert connection is not None
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
                assert connection is not None
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
            connection.sync_revision if connection else None,
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
        if operation == "sync":
            assert connection is not None
            connector = payload.get("connector") or connector_snapshot(connection)
        else:
            connector = None
        snapshot = product_snapshot(product) if product else None
        token = start_job(job, utcnow(), 360)
        fields = job_fields(job)
    logger.info("partner_pipeline_started", extra=fields)
    error, retryable, retry_after = None, True, 0
    products: Sequence[ProductCandidate | ProductInput] = []
    cursor: str | None = None
    result: Qualification | None = None
    error_fields: dict[str, str | int | None] = {}
    assessment_stage = "preparation"
    try:
        if operation == "sync":
            if provider is None or connection is None or connector is None:
                raise ValueError("Missing import connection API snapshot")
            products, cursor = (
                reader(payload["cursor"])
                if reader
                else ConnectorClient(
                    provider, connector, credential=connection_credential(connection, connector)
                ).discover_page(payload["cursor"])
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
                raise ValueError("Import API pagination did not advance")
        else:
            if snapshot is None:
                raise ValueError("Missing product snapshot")
            assessment_stage = "website_fetch"
            page = (page_fetcher or fetched_page)(snapshot["product_url"], 15)
            page = {"final_url": page["final_url"], "text": page["text"][:20000]}
            assessment_stage = "redirect_verification"

            # Resolve observed redirects before paying for a second assessment of the same tool.
            def acquire_current(session: Session) -> PartnerPipelineJob | None:
                return session.scalar(
                    select(PartnerPipelineJob)
                    .where(PartnerPipelineJob.id == identifier)
                    .with_for_update(nowait=True)
                )

            with claim_partner_job(factory, acquire_current) as (session, current_job):
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
                    or not product_has_active_source(session, current_product.id)
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
            assessment_stage = "model_inference"
            raw = (assessor or qualify)(snapshot, page)
            assessment_stage = "qualification_validation"
            result = validate_qualification(raw, page)
    except QualificationError as exc:
        error = str(exc)
        error_fields = {
            "error_type": type(exc).__name__,
            "diagnostic_code": exc.code,
            "assessment_stage": assessment_stage,
        }
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
    if error and operation == "assess":
        error_fields.setdefault("diagnostic_code", "assessment_failed")
        error_fields.setdefault("assessment_stage", assessment_stage)

    def acquire_result(
        session: Session,
    ) -> tuple[PartnerConnection | None, PartnerPipelineJob | None]:
        connection = session.scalar(
            select(PartnerConnection)
            .where(PartnerConnection.provider == provider)
            .with_for_update(nowait=True)
        )
        job = session.scalar(
            select(PartnerPipelineJob)
            .where(PartnerPipelineJob.id == identifier)
            .with_for_update(nowait=True)
        )
        return connection, job

    with claim_partner_job(factory, acquire_result) as (session, claimed_result):
        connection, job = claimed_result
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
                and connection.archived_at is None
                and connection.sync_revision == connection_revision
            )
            if operation == "sync"
            else product_has_active_source(session, job.product_id)
        )
        if not available:
            logger.info(
                "partner_pipeline_cancelled",
                extra={**job_fields(job), "reason": "connection_changed"},
            )
            fail_or_retry(
                job,
                "Product source is unavailable" if operation == "assess" else "Connection changed",
                utcnow(),
                retryable=False,
            )
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
            if operation == "assess":
                job.payload = {
                    **job.payload,
                    "diagnostic": {
                        "code": error_fields["diagnostic_code"],
                        "stage": error_fields["assessment_stage"],
                    },
                }
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
                assert connection is not None
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
                assert connection is not None
                finish_discovery(session, job, connection)
            return
        else:
            assert product is not None and result is not None
            job.payload = {key: value for key, value in job.payload.items() if key != "diagnostic"}
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
            if result.decision == "qualified":
                from devfeed_core.logos import request_logo
                from devfeed_core.partner_product_images import request_product_image

                request_logo(session, PartnerProduct, product.id, automatic=True)
                request_product_image(session, product.id, automatic=True)
            from devfeed_core.partner_promotions import ensure_automatic_assets

            ensure_automatic_assets(session, product_ids=[product.id])
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


def schedule_partner_syncs(factory: sessionmaker[Session]) -> None:
    now = utcnow()
    with factory.begin() as session:
        lock_catalog(session)
        connections = session.scalars(
            select(PartnerConnection)
            .where(PartnerConnection.enabled.is_(True), PartnerConnection.archived_at.is_(None))
            .with_for_update(skip_locked=True)
        ).all()
        for connection in connections:
            interval = connection_sync_interval(session, connection)
            if interval != connection.sync_interval_minutes and connection.last_sync_at:
                connection.next_sync_at = max(
                    connection.next_sync_at,
                    connection.last_sync_at + timedelta(minutes=interval),
                )
            if connection.next_sync_at <= now:
                job = request_sync(session, connection)
                created = job in session.new
                session.flush()
                if created:
                    logger.info(
                        "partner_sync_scheduled", extra={**job_fields(job), "trigger": "schedule"}
                    )
        # Qualification belongs to products, independent of whichever platform synced first.
        recent_failure = (
            select(PartnerPipelineJob.id)
            .where(
                PartnerPipelineJob.product_id == PartnerProduct.id,
                PartnerPipelineJob.operation == "assess",
                PartnerPipelineJob.status == "failed",
                PartnerPipelineJob.payload["revision"].as_integer() == PartnerProduct.revision,
                PartnerPipelineJob.finished_at > now - ASSESSMENT_RENEWAL_RETRY_INTERVAL,
            )
            .exists()
        )
        products = session.scalars(
            select(PartnerProduct)
            .where(
                PartnerProduct.merged_into_id.is_(None),
                has_active_source(),
                PartnerProduct.excluded.is_(False),
                PartnerProduct.status != "withdrawn",
                or_(
                    PartnerProduct.assessment_revision != PartnerProduct.revision,
                    and_(
                        PartnerProduct.status == "approved",
                        PartnerProduct.verified_at
                        <= now - (QUALIFICATION_TTL - ASSESSMENT_RENEWAL_LEAD_TIME),
                        ~recent_failure,
                    ),
                ),
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
            request_assessment(
                session, product, renewal=product.assessment_revision == product.revision
            )


def dispatch_partner_pipeline(
    factory: sessionmaker[Session], limit: int | None = None, *, schedule: bool = True
) -> int:
    if limit is not None and limit < 1:
        raise ValueError("Partner dispatch limit must be positive")
    if schedule:
        schedule_partner_syncs(factory)
    now, dispatched = utcnow(), 0
    child = aliased(PartnerPipelineJob)
    statement = (
        select(PartnerPipelineJob)
        .where(
            runnable(now),
            or_(
                PartnerPipelineJob.dispatched_at.is_(None),
                PartnerPipelineJob.dispatched_at < now - timedelta(seconds=60),
            ),
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
            ),
        )
        .order_by(
            PartnerPipelineJob.dispatched_at.asc().nulls_first(),
            PartnerPipelineJob.available_at,
            PartnerPipelineJob.created_at,
            PartnerPipelineJob.id,
        )
    )
    settings = get_settings()
    outbox = PartnerOutbox(factory, get_queue, batch_size=settings.scheduler_batch_size, now=now)
    lanes = [("ingestion", ["sync", "sync_product"])]
    if settings.ai_enabled:
        lanes.append(("source-analysis", ["assess"]))
    turn_context = (
        _pipeline_turn(factory, statement, settings.scheduler_batch_size)
        if settings.ai_enabled
        else nullcontext(_PipelineTurn(deadline=time.monotonic() + PUBLICATION_BATCH_SECONDS))
    )
    error = None
    with turn_context as turn:
        if turn is None:
            return 0
        if turn.assessment_first:
            lanes.reverse()
        for queue_name, operations in lanes:

            def delivery_id(job: PartnerPipelineJob, queue_name: str = queue_name) -> str:
                # Routing keeps an old optional-queue delivery from suppressing active workers.
                return (
                    f"partner-pipeline-{queue_name}-{job.id}-{job.payload.get('pages', 0)}-"
                    f"{job.payload.get('finalize_batches', 0)}-{job.attempts}"
                )

            try:
                dispatched += outbox.publish(
                    queue_name,
                    statement.where(PartnerPipelineJob.operation.in_(operations)),
                    model=PartnerPipelineJob,
                    limit=limit,
                    handler="devfeed_aggregator.partner_sync_tasks.process_pipeline",
                    delivery_id=delivery_id,
                    event="partner_pipeline_dispatched",
                    fields=job_fields,
                    session=turn.session,
                    pending_logs=turn.pending_logs,
                    deadline=turn.deadline,
                )
            except Exception as exc:
                # A failed sync delivery must not consume assessment's publication budget.
                if error is None:
                    error = exc
        turn.published = dispatched
    if error is not None:
        raise error
    return dispatched

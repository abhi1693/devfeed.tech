"""Launch platform connections and their API-managed catalog, private to admins."""

import logging
import uuid
from datetime import timedelta

from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import (
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_catalog import active_provider, has_listings, listing_views, lock_catalog
from devfeed_core.partner_connections import (
    ConnectionAction,
    ConnectionCreate,
    ConnectionOut,
    ConnectionSettings,
    PartnerJobOut,
    ProductAction,
    request_assessment,
    request_sync,
)
from devfeed_core.partner_providers import SUPPORTED_PARTNERS, PartnerProviderOut
from devfeed_core.partner_tools import EvaluationOut, ProductOut, product_view, snapshot_current
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/admin/partner-tools",
    tags=["admin-partner-tools"],
    dependencies=[Depends(require_admin)],
)


def connection_view(session, connection):
    provider = connection.provider
    metadata = SUPPORTED_PARTNERS[provider]

    belongs = exists(
        select(PartnerListing.id).where(
            PartnerListing.product_id == PartnerProduct.id, PartnerListing.provider == provider
        )
    )
    counts = dict(
        session.execute(
            select(PartnerProduct.status, func.count())
            .where(belongs, PartnerProduct.excluded.is_(False))
            .group_by(PartnerProduct.status)
        ).all()
    )
    excluded = (
        session.scalar(
            select(func.count())
            .select_from(PartnerProduct)
            .where(belongs, PartnerProduct.excluded.is_(True))
        )
        or 0
    )
    checking = (
        session.scalar(
            select(func.count())
            .select_from(PartnerProduct)
            .where(
                belongs,
                PartnerProduct.excluded.is_(False),
                PartnerProduct.assessment["state"].astext == "checking",
            )
        )
        or 0
    )
    active = session.scalar(
        select(PartnerPipelineJob.id).where(
            PartnerPipelineJob.provider == provider,
            PartnerPipelineJob.operation == "sync",
            PartnerPipelineJob.status.in_(["queued", "running"]),
        )
    )
    return ConnectionOut(
        provider=provider,
        revision=connection.revision,
        sync_interval_minutes=connection.sync_interval_minutes,
        name=metadata.name,
        api_url=metadata.api_url,
        enabled=bool(connection and connection.enabled),
        state="disconnected"
        if not connection or not connection.enabled
        else "syncing"
        if active
        else "error"
        if connection.last_error
        else "idle",
        last_sync_at=connection.last_sync_at if connection else None,
        next_sync_at=connection.next_sync_at if connection and connection.enabled else None,
        error=connection.last_error if connection else None,
        products=sum(counts.values()) + excluded,
        qualified=counts.get("approved", 0),
        checking=checking,
        needs_attention=max(0, counts.get("pending", 0) - checking),
        excluded=excluded,
        ai_enabled=get_settings().ai_enabled,
    )


@router.get(
    "/connections",
    response_model=list[ConnectionOut],
    operation_id="admin_partner_connections_list",
)
def connections(session: DB):
    records = session.scalars(
        select(PartnerConnection)
        .where(PartnerConnection.provider.in_(SUPPORTED_PARTNERS))
        .order_by(PartnerConnection.provider)
    ).all()
    return [connection_view(session, connection) for connection in records]


@router.get(
    "/providers",
    response_model=list[PartnerProviderOut],
    operation_id="admin_partner_providers_list",
)
def providers():
    return list(SUPPORTED_PARTNERS.values())


def configure_connection(session, connection, enabled, *, sync=False, sync_interval_minutes=None):
    affected_jobs = []
    changed = connection.enabled != enabled
    interval_changed = (
        sync_interval_minutes is not None
        and sync_interval_minutes != connection.sync_interval_minutes
    )
    if changed or interval_changed:
        connection.revision += 1
    if changed:
        connection.enabled = enabled
        connection.sync_revision += 1
    if interval_changed:
        connection.sync_interval_minutes = sync_interval_minutes
        if enabled:
            now = utcnow()
            connection.next_sync_at = max(
                now, (connection.last_sync_at or now) + timedelta(minutes=sync_interval_minutes)
            )
    if not enabled and changed:
        for job in session.scalars(
            select(PartnerPipelineJob)
            .where(
                PartnerPipelineJob.provider == connection.provider,
                PartnerPipelineJob.status.in_(["queued", "running"]),
            )
            .with_for_update()
        ).all():
            if job.operation in {"sync", "sync_product"} or not active_provider(
                session, job.product_id
            ):
                affected_jobs.append(str(job.id))
                fail_or_retry(job, "Connection disabled", utcnow(), retryable=False)
    elif enabled and (changed or sync):
        job = request_sync(session, connection)
        session.flush()
        affected_jobs.append(str(job.id))
    return affected_jobs


def log_connection(event, connection, admin, affected_jobs):
    logger.info(
        event,
        extra={
            "provider": connection.provider,
            "partnership_type": connection.partnership_type,
            "connection_revision": connection.revision,
            "enabled": connection.enabled,
            "sync_interval_minutes": connection.sync_interval_minutes,
            "actor_subject": admin.subject,
            "job_ids": affected_jobs,
            "job_id": affected_jobs[0] if connection.enabled and affected_jobs else None,
            "jobs_cancelled": len(affected_jobs) if not connection.enabled else 0,
        },
    )


@router.post(
    "/connections",
    response_model=ConnectionOut,
    status_code=201,
    operation_id="admin_partner_connection_create",
)
def create_connection(body: ConnectionCreate, session: DB, admin: Admin):
    lock_catalog(session)
    if body.provider not in SUPPORTED_PARTNERS:
        raise HTTPException(422, "Choose a supported partner")
    if session.get(PartnerConnection, body.provider):
        raise HTTPException(409, "This partner has already been added; edit its settings")
    connection = PartnerConnection(
        provider=body.provider,
        enabled=False,
        updated_by=actor(admin),
        sync_interval_minutes=body.sync_interval_minutes,
    )
    session.add(connection)
    session.flush()
    affected_jobs = configure_connection(session, connection, body.enabled)
    session.commit()
    log_connection("partner_connection_added", connection, admin, affected_jobs)
    return connection_view(session, connection)


@router.put(
    "/connections/{provider}",
    response_model=ConnectionOut,
    operation_id="admin_partner_connection_update",
)
def update_connection(provider: str, body: ConnectionSettings, session: DB, admin: Admin):
    lock_catalog(session)
    connection = (
        session.get(PartnerConnection, provider) if provider in SUPPORTED_PARTNERS else None
    )
    if not connection:
        raise HTTPException(404, "Partner connection not found")
    if connection.revision != body.expected_revision:
        raise HTTPException(
            409, "Partner settings changed; close and reopen the form to load the latest settings"
        )
    affected_jobs = configure_connection(
        session, connection, body.enabled, sync_interval_minutes=body.sync_interval_minutes
    )
    connection.updated_by = actor(admin)
    session.commit()
    log_connection("partner_connection_settings_updated", connection, admin, affected_jobs)
    return connection_view(session, connection)


@router.post(
    "/connections/{provider}",
    response_model=ConnectionOut,
    operation_id="admin_partner_connection_action",
)
def connection_action(provider: str, body: ConnectionAction, session: DB, admin: Admin):
    lock_catalog(session)
    if provider not in SUPPORTED_PARTNERS:
        raise HTTPException(404, "Launch platform connection not found")
    session.execute(insert(PartnerConnection).values(provider=provider).on_conflict_do_nothing())
    connection = session.scalar(
        select(PartnerConnection).where(PartnerConnection.provider == provider).with_for_update()
    )
    assert connection is not None
    if body.action == "sync" and not connection.enabled:
        raise HTTPException(409, "Enable this partner before syncing")
    affected_jobs = configure_connection(
        session, connection, body.action != "pause", sync=body.action != "pause"
    )
    connection.updated_by = actor(admin)
    session.commit()
    log_connection(
        {
            "connect": "partner_connection_connected",
            "pause": "partner_connection_paused",
            "sync": "partner_sync_requested",
        }[body.action],
        connection,
        admin,
        affected_jobs,
    )
    return connection_view(session, connection)


@router.get(
    "/connections/{provider}/jobs",
    response_model=Page[PartnerJobOut],
    operation_id="admin_partner_connection_jobs",
)
def connection_jobs(provider: str, session: DB, query: Listing):
    if provider not in SUPPORTED_PARTNERS or not session.get(PartnerConnection, provider):
        raise HTTPException(404, "Partner connection not found")
    shared_product = exists(
        select(PartnerListing.id).where(
            PartnerListing.provider == provider,
            PartnerListing.product_id == PartnerPipelineJob.product_id,
        )
    )
    statement = select(PartnerPipelineJob).where(
        or_(
            PartnerPipelineJob.provider == provider,
            (PartnerPipelineJob.operation == "assess") & shared_product,
        )
    )
    return paginate(
        session,
        statement,
        query,
        {
            "created_at": PartnerPipelineJob.created_at,
            "status": PartnerPipelineJob.status,
        },
        "-created_at",
    )


@router.get("", response_model=Page[ProductOut], operation_id="admin_partner_tools_list")
def listing(
    session: DB, query: Listing, provider: str | None = None, product_id: uuid.UUID | None = None
):
    statement = select(PartnerProduct).where(
        PartnerProduct.merged_into_id.is_(None), has_listings()
    )
    if provider is not None:
        statement = statement.where(
            exists(
                select(PartnerListing.id).where(
                    PartnerListing.product_id == PartnerProduct.id,
                    PartnerListing.provider == provider,
                )
            )
        )
    if product_id is not None:
        statement = statement.where(PartnerProduct.id == product_id)
    if query.q:
        statement = statement.where(PartnerProduct.name.ilike(f"%{query.q}%"))
    page = paginate(
        session,
        statement,
        query,
        {"name": PartnerProduct.name, "updated_at": PartnerProduct.updated_at},
        "-updated_at",
    )
    listings = listing_views(session, [p.id for p in page["items"]])
    return {**page, "items": [product_view(p, listings[p.id]) for p in page["items"]]}


@router.post(
    "/{product_id}/actions", response_model=ProductOut, operation_id="admin_partner_product_action"
)
def product_action(product_id: uuid.UUID, body: ProductAction, session: DB, admin: Admin):
    lock_catalog(session)
    product = record(session, PartnerProduct, product_id)
    if product.merged_into_id or not listing_views(session, [product.id])[product.id]:
        raise HTTPException(404, "API-managed product not found")
    if body.action == "retry" and not active_provider(session, product_id):
        raise HTTPException(409, "An active platform listing is required for rechecking")
    pipeline_jobs = session.scalars(
        select(PartnerPipelineJob)
        .where(
            PartnerPipelineJob.product_id == product_id,
            PartnerPipelineJob.status.in_(["queued", "running"]),
        )
        .with_for_update()
    ).all()
    evaluations = session.scalars(
        select(PartnerEvaluation)
        .where(
            PartnerEvaluation.product_id == product_id,
            PartnerEvaluation.status.in_(["queued", "running"]),
        )
        .with_for_update()
    ).all()
    for job in pipeline_jobs:
        fail_or_retry(job, "Product action superseded this check", utcnow(), retryable=False)
    for evaluation in evaluations:
        fail_or_retry(evaluation, "Product action superseded this check", utcnow(), retryable=False)
    product = session.scalar(
        select(PartnerProduct)
        .where(PartnerProduct.id == product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert product is not None
    if product.revision != body.expected_revision:
        raise HTTPException(409, "Product changed; refresh and try again")
    if product.status == "withdrawn" and body.action == "retry":
        raise HTTPException(409, "This product is no longer available from the platform")
    product.excluded = body.action == "exclude"
    product.status = "paused" if product.excluded else "pending"
    product.revision += 1
    product.verified_at = None
    product.updated_at = utcnow()
    product.reviews = [
        *product.reviews,
        {"actor": actor(admin), "action": body.action, "note": "", "at": utcnow().isoformat()},
    ]
    session.flush()
    if not product.excluded:
        request_assessment(session, product)
    session.commit()
    logger.info(
        "partner_product_excluded" if product.excluded else "partner_product_recheck_requested",
        extra={
            "product_id": str(product.id),
            "product_revision": product.revision,
            "actor_subject": admin.subject,
        },
    )
    return product_view(product, listing_views(session, [product.id])[product.id])


def evaluation_view(session, job):
    return {
        **{
            key: getattr(job, key)
            for key in (
                "id",
                "product_id",
                "status",
                "created_at",
                "finished_at",
                "error",
                "snapshot",
                "reviews",
            )
        },
        "result": job.result or None,
        "current": snapshot_current(session, job),
    }


@router.get(
    "/{product_id}/evaluations",
    response_model=list[EvaluationOut],
    operation_id="admin_partner_tools_evaluations",
)
def evaluations(product_id: uuid.UUID, session: DB):
    product = record(session, PartnerProduct, product_id)
    if product.merged_into_id or not listing_views(session, [product.id])[product.id]:
        raise HTTPException(404, "API-managed product not found")
    jobs = session.scalars(
        select(PartnerEvaluation)
        .where(PartnerEvaluation.product_id == product_id)
        .order_by(PartnerEvaluation.created_at.desc())
        .limit(10)
    ).all()
    return [evaluation_view(session, job) for job in jobs]

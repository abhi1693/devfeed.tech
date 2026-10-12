"""Launch platform connections and their API-managed catalog, private to admins."""

import logging
import uuid
from datetime import timedelta
from typing import Literal

from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import fail_or_retry
from devfeed_core.models import (
    PartnerAccount,
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from devfeed_core.partner_catalog import active_provider, has_listings, listing_views, lock_catalog
from devfeed_core.partner_connections import (
    ConnectionAction,
    ConnectionCreate,
    ConnectionOut,
    ConnectionSettings,
    ConnectorPreview,
    ConnectorPreviewOut,
    ConnectorResponseOut,
    PartnerJobOut,
    ProductAction,
    read_partner_page,
    read_partner_product,
    request_assessment,
    request_sync,
)
from devfeed_core.partner_connectors import (
    CONNECTOR_PRESETS,
    ConnectorConfig,
    connector_snapshot,
    response_preview,
)
from devfeed_core.partner_providers import PARTNER_PRESETS, PartnerProviderOut
from devfeed_core.partner_tools import EvaluationOut, ProductOut, product_view, snapshot_current
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.search import text_search

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/admin/partner-tools",
    tags=["admin-partner-tools"],
    dependencies=[Depends(require_admin)],
)


def connection_views(session, connections):
    """Aggregate catalog statistics for the supplied bounded connection page."""
    if not connections:
        return []
    providers = [connection.provider for connection in connections]
    products = (
        select(PartnerListing.provider, PartnerListing.product_id)
        .where(PartnerListing.provider.in_(providers))
        .distinct()
        .subquery()
    )
    owner_connection = aliased(PartnerConnection)
    owner_listing = aliased(PartnerListing)
    # Provider membership includes historical listings. Live ownership may come
    # from another enabled connection on the same account.
    owned = exists(
        select(owner_listing.id)
        .join(owner_connection, owner_connection.provider == owner_listing.provider)
        .where(
            owner_connection.account_id == PartnerConnection.account_id,
            owner_connection.enabled.is_(True),
            owner_listing.product_id == PartnerProduct.id,
            owner_listing.active.is_(True),
            owner_listing.identity_status == "resolved",
        )
        .correlate(PartnerConnection, PartnerProduct)
    )
    accepted = and_(
        PartnerConnection.enabled.is_(True),
        PartnerAccount.status == "active",
        PartnerProduct.merged_into_id.is_(None),
        PartnerProduct.excluded.is_(False),
        or_(
            PartnerProductPromotion.product_id.is_(None),
            PartnerProductPromotion.excluded.is_(False),
        ),
        owned,
        or_(
            and_(
                PartnerProduct.status == "approved",
                PartnerProduct.verified_at >= utcnow() - timedelta(days=90),
            ),
            and_(
                PartnerAccount.approval_mode == "trusted",
                PartnerProductPromotion.commercial_approved_at.is_not(None),
            ),
        ),
    )
    syncing = exists(
        select(PartnerPipelineJob.id)
        .where(
            PartnerPipelineJob.provider == PartnerConnection.provider,
            PartnerPipelineJob.operation == "sync",
            PartnerPipelineJob.status.in_(["queued", "running"]),
        )
        .correlate(PartnerConnection)
    )
    count = func.count(PartnerProduct.id)
    statistics = {
        row.provider: row
        for row in session.execute(
            select(
                PartnerConnection.provider,
                PartnerAccount.approval_mode,
                syncing.label("syncing"),
                count.label("products"),
                count.filter(
                    PartnerProduct.excluded.is_(False), PartnerProduct.status == "approved"
                ).label("qualified"),
                count.filter(
                    PartnerProduct.excluded.is_(False),
                    PartnerProduct.assessment["state"].astext == "checking",
                ).label("checking"),
                count.filter(
                    PartnerProduct.excluded.is_(False), PartnerProduct.status == "pending"
                ).label("pending"),
                count.filter(PartnerProduct.excluded.is_(True)).label("excluded"),
                count.filter(accepted).label("commercial_approved"),
            )
            .select_from(PartnerConnection)
            .outerjoin(PartnerAccount, PartnerAccount.id == PartnerConnection.account_id)
            .outerjoin(products, products.c.provider == PartnerConnection.provider)
            .outerjoin(PartnerProduct, PartnerProduct.id == products.c.product_id)
            .outerjoin(
                PartnerProductPromotion,
                and_(
                    PartnerProductPromotion.account_id == PartnerConnection.account_id,
                    PartnerProductPromotion.product_id == PartnerProduct.id,
                ),
            )
            .where(PartnerConnection.provider.in_(providers))
            .group_by(PartnerConnection.provider, PartnerAccount.id)
        )
    }
    ai_enabled = get_settings().ai_enabled
    return [
        _connection_view(connection, statistics[connection.provider], ai_enabled=ai_enabled)
        for connection in connections
    ]


def connection_view(session, connection):
    return connection_views(session, [connection])[0]


def _connection_view(connection, statistics, *, ai_enabled):
    provider = connection.provider
    definition = (
        connector_snapshot(connection)
        if connection.connector or provider in CONNECTOR_PRESETS
        else None
    )

    return ConnectionOut(
        commercial_approved=statistics.commercial_approved,
        approval_mode=statistics.approval_mode,
        provider=provider,
        account_id=connection.account_id,
        revision=connection.revision,
        sync_interval_minutes=connection.sync_interval_minutes,
        name=connection.name or provider.replace("-", " ").title(),
        api_url=definition["base_url"] + definition["list_path"] if definition else "",
        connector=definition,
        enabled=connection.enabled,
        state="disconnected"
        if not connection.enabled
        else "syncing"
        if statistics.syncing
        else "error"
        if connection.last_error
        else "idle",
        last_sync_at=connection.last_sync_at,
        next_sync_at=connection.next_sync_at if connection.enabled else None,
        error=connection.last_error,
        products=statistics.products,
        qualified=statistics.qualified,
        checking=statistics.checking,
        needs_attention=max(0, statistics.pending - statistics.checking),
        excluded=statistics.excluded,
        ai_enabled=ai_enabled,
    )


@router.get(
    "/connections",
    response_model=Page[ConnectionOut],
    operation_id="admin_partner_connections_list",
)
def connections(query: Listing, session: DB):
    statement = select(PartnerConnection)
    if query.q:
        statement = statement.where(
            text_search(query.q, PartnerConnection.provider, PartnerConnection.name)
        )
    result = paginate(
        session,
        statement,
        query,
        {"provider": PartnerConnection.provider, "name": PartnerConnection.name},
        default="provider",
    )
    result["items"] = connection_views(session, result["items"])
    return result


@router.get(
    "/connections/{provider}",
    response_model=ConnectionOut,
    operation_id="admin_partner_connection_get",
)
def connection(provider: str, session: DB):
    value = session.get(PartnerConnection, provider)
    if value is None:
        raise HTTPException(404, "Import connection not found")
    return connection_view(session, value)


@router.get(
    "/providers",
    response_model=list[PartnerProviderOut],
    operation_id="admin_partner_providers_list",
)
def providers():
    return list(PARTNER_PRESETS.values())


@router.post(
    "/connector-preview",
    response_model=ConnectorPreviewOut,
    operation_id="admin_partner_connector_preview",
)
def preview_connector(body: ConnectorPreview, admin: Admin):
    # Bounded, read-only preview: one discovery page and at most three detail requests.
    try:
        candidates, cursor = read_partner_page(
            body.provider, config=body.connector.model_dump(mode="json")
        )
    except Exception as exc:
        logger.info(
            "partner_connector_preview_failed",
            extra={"provider": body.provider, "error_type": type(exc).__name__},
        )
        raise HTTPException(
            422, "Could not read the product page. Check endpoints, response paths and credentials."
        ) from exc
    products, errors = [], []
    for candidate in candidates[:3]:
        try:
            products.append(
                read_partner_product(
                    body.provider, candidate, body.connector.model_dump(mode="json")
                )
            )
        except Exception:
            errors.append(
                "A product could not be mapped. Check field paths, detail endpoint and URLs."
            )
    return ConnectorPreviewOut(
        products=products, errors=errors, has_next_page=bool(cursor), discovered=len(candidates)
    )


@router.post(
    "/connector-response",
    response_model=ConnectorResponseOut,
    operation_id="admin_partner_connector_response",
)
def connector_response(body: ConnectorPreview, admin: Admin):
    try:
        return response_preview(body.provider, body.connector)
    except Exception as exc:
        raise HTTPException(
            422, "Could not read the API response. Check the endpoint and authentication."
        ) from exc


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
            "account_id": str(connection.account_id) if connection.account_id else None,
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
    if body.connector is None and body.provider not in CONNECTOR_PRESETS:
        raise HTTPException(422, "Provide an API connector definition")
    if body.name is None and body.provider not in PARTNER_PRESETS:
        raise HTTPException(422, "Provide an import connection name")
    if session.get(PartnerConnection, body.provider):
        raise HTTPException(409, "This import connection already exists; edit its settings")
    if body.account_id is not None and session.get(PartnerAccount, body.account_id) is None:
        raise HTTPException(422, "Partner account not found")
    connection = PartnerConnection(
        account_id=body.account_id,
        provider=body.provider,
        name=body.name or PARTNER_PRESETS[body.provider].name,
        connector=(
            body.connector or ConnectorConfig.model_validate(CONNECTOR_PRESETS[body.provider])
        ).model_dump(mode="json"),
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
    connection = session.get(PartnerConnection, provider)
    if not connection:
        raise HTTPException(404, "Import connection not found")
    if connection.revision != body.expected_revision:
        raise HTTPException(
            409, "Import connection settings changed; reopen the form to load the latest settings"
        )
    if "account_id" in body.model_fields_set and body.account_id != connection.account_id:
        if body.account_id is not None and session.get(PartnerAccount, body.account_id) is None:
            raise HTTPException(422, "Partner account not found")
        connection.account_id = body.account_id
        connection.revision += 1
    definition_changed = body.connector is not None and body.connector.model_dump(
        mode="json"
    ) != connector_snapshot(connection)
    if body.name is not None and body.name != connection.name:
        connection.name = body.name
        connection.revision += 1
    if definition_changed:
        assert body.connector is not None
        connection.connector = body.connector.model_dump(mode="json")
        connection.revision += 1
        connection.sync_revision += 1
        for job in session.scalars(
            select(PartnerPipelineJob)
            .where(
                PartnerPipelineJob.provider == provider,
                PartnerPipelineJob.operation.in_(["sync", "sync_product"]),
                PartnerPipelineJob.status.in_(["queued", "running"]),
            )
            .with_for_update()
        ):
            fail_or_retry(job, "Connector definition changed", utcnow(), retryable=False)
        session.flush()
    affected_jobs = configure_connection(
        session,
        connection,
        body.enabled,
        sync_interval_minutes=body.sync_interval_minutes,
        sync=definition_changed,
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
    if not session.get(PartnerConnection, provider):
        if provider not in PARTNER_PRESETS:
            raise HTTPException(404, "Import connection not found")
        session.execute(
            insert(PartnerConnection)
            .values(
                provider=provider,
                name=PARTNER_PRESETS[provider].name,
                connector=ConnectorConfig.model_validate(CONNECTOR_PRESETS[provider]).model_dump(
                    mode="json"
                ),
            )
            .on_conflict_do_nothing()
        )
    connection = session.scalar(
        select(PartnerConnection).where(PartnerConnection.provider == provider).with_for_update()
    )
    assert connection is not None
    if body.action == "sync" and not connection.enabled:
        raise HTTPException(409, "Enable this import connection before syncing")
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
    if not session.get(PartnerConnection, provider):
        raise HTTPException(404, "Import connection not found")
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
    if query.q:
        statement = statement.where(
            or_(
                *[
                    column.icontains(query.q, autoescape=True)
                    for column in (
                        cast(PartnerPipelineJob.id, String),
                        PartnerPipelineJob.external_id,
                        PartnerPipelineJob.provider,
                    )
                ]
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
    session: DB,
    query: Listing,
    provider: str | None = None,
    product_id: uuid.UUID | None = None,
    status: Literal["pending", "approved", "rejected", "paused", "withdrawn"] | None = None,
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
    if status is not None:
        statement = statement.where(PartnerProduct.status == status)
    if product_id is not None:
        statement = statement.where(PartnerProduct.id == product_id)
    if query.q:
        statement = statement.where(PartnerProduct.name.icontains(query.q, autoescape=True))
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

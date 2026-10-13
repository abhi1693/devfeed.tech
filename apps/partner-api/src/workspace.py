"""User-wide partner views scoped in SQL to every current account membership."""

import uuid

from devfeed_core.models import PartnerAccount, PartnerConnection
from devfeed_core.partner_ad_formats import AdFormat
from devfeed_core.partner_credentials import credential_storage_available
from devfeed_core.text_search import text_search
from devfeed_http.pagination import Listing, Page
from devfeed_http.partner_portal import (
    SortOrder,
    WorkspaceAssetSort,
    WorkspaceDashboard,
    WorkspaceInventoryOut,
    inventory_page,
    reporting_metrics,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from devfeed_partner_api.auth import Partner, require_partner
from devfeed_partner_api.connections import (
    PartnerConnectionCreate,
    PartnerConnectionJobsOut,
    PartnerConnectionOut,
    connection_jobs,
    create_connection,
    partner_connection_views,
)
from devfeed_partner_api.dependencies import DB
from devfeed_partner_api.portal import account_access, visible_accounts

router = APIRouter(
    prefix="/v1/partner", tags=["partner-workspace"], dependencies=[Depends(require_partner)]
)


class WorkspaceConnectionOut(PartnerConnectionOut):
    account_id: uuid.UUID
    account_name: str
    credentials_available: bool


class WorkspaceConnectionsOut(Page[WorkspaceConnectionOut]):
    credentials_available: bool


class WorkspaceConnectionCreate(PartnerConnectionCreate):
    account_id: uuid.UUID


def workspace_accounts(session, partner, account_id=None):
    scope = visible_accounts(partner)
    if account_id is not None:
        account_access(session, partner, account_id)
        scope = scope.where(PartnerAccount.id == account_id)
    return scope


@router.get(
    "/dashboard", response_model=WorkspaceDashboard, operation_id="partnerWorkspaceDashboard"
)
def dashboard(
    session: DB,
    partner: Partner,
    account_id: uuid.UUID | None = None,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: WorkspaceAssetSort = Query("name"),
    order: SortOrder = Query("asc"),
    ad_format: AdFormat | None = None,
):
    scope = workspace_accounts(session, partner, account_id)
    return {
        **reporting_metrics(
            session, scope, days, limit, offset, sort=sort, order=order, ad_format=ad_format
        ),
        "account_total": session.scalar(select(func.count()).select_from(scope.subquery())),
    }


@router.get(
    "/inventory", response_model=WorkspaceInventoryOut, operation_id="partnerWorkspaceInventory"
)
def inventory(
    session: DB,
    partner: Partner,
    account_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archived: bool = False,
):
    return inventory_page(
        session,
        workspace_accounts(session, partner, account_id),
        q=q,
        product_id=product_id,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
    )


def connection_query(partner):
    return (
        select(PartnerConnection, PartnerAccount.name.label("account_name"))
        .join(PartnerAccount, PartnerAccount.id == PartnerConnection.account_id)
        .where(
            PartnerConnection.archived_at.is_(None),
            PartnerAccount.id.in_(visible_accounts(partner).with_only_columns(PartnerAccount.id)),
        )
    )


def workspace_connection(session, partner, provider):
    row = session.execute(
        connection_query(partner).where(PartnerConnection.provider == provider)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Import connection not found")
    return row


def connection_view(session, connection, account_name):
    view = partner_connection_views(session, [connection])[0]
    return WorkspaceConnectionOut(
        **view.model_dump(),
        account_name=account_name,
        credentials_available=credential_storage_available(),
    )


@router.get(
    "/connections",
    response_model=WorkspaceConnectionsOut,
    operation_id="partnerWorkspaceConnections",
)
def connections(session: DB, partner: Partner, query: Listing, account_id: uuid.UUID | None = None):
    if account_id is not None:
        account_access(session, partner, account_id)
    statement = connection_query(partner)
    if account_id is not None:
        statement = statement.where(PartnerConnection.account_id == account_id)
    if query.q:
        statement = statement.where(
            text_search(
                query.q, PartnerConnection.name, PartnerConnection.provider, PartnerAccount.name
            )
        )
    sort = query.sort or "name"
    column = {
        "name": PartnerConnection.name,
        "provider": PartnerConnection.provider,
        "account": PartnerAccount.name,
    }.get(sort.removeprefix("-"))
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = session.execute(
        statement.order_by(
            column.desc() if sort.startswith("-") else column.asc(), PartnerConnection.provider
        )
        .limit(query.limit)
        .offset(query.offset)
    ).all()
    views = partner_connection_views(session, [row[0] for row in rows])
    available = credential_storage_available()
    return {
        "items": [
            WorkspaceConnectionOut(
                **view.model_dump(), account_name=row.account_name, credentials_available=available
            )
            for row, view in zip(rows, views, strict=True)
        ],
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
        "credentials_available": available,
    }


@router.get(
    "/connections/{provider}",
    response_model=WorkspaceConnectionOut,
    operation_id="partnerWorkspaceConnectionGet",
)
def get_connection(provider: str, session: DB, partner: Partner):
    connection, account_name = workspace_connection(session, partner, provider)
    return connection_view(session, connection, account_name)


@router.get(
    "/connections/{provider}/jobs",
    response_model=PartnerConnectionJobsOut,
    operation_id="partnerWorkspaceConnectionJobs",
)
def jobs(provider: str, session: DB, partner: Partner, query: Listing):
    connection, _ = workspace_connection(session, partner, provider)
    return connection_jobs(connection.account_id, provider, session, partner, query)


@router.post(
    "/connections",
    response_model=WorkspaceConnectionOut,
    status_code=201,
    operation_id="partnerWorkspaceConnectionCreate",
)
def add_connection(body: WorkspaceConnectionCreate, session: DB, partner: Partner):
    payload = PartnerConnectionCreate.model_validate(
        {field: getattr(body, field) for field in PartnerConnectionCreate.model_fields}
    )
    view = create_connection(body.account_id, payload, session, partner)
    account = account_access(session, partner, body.account_id)
    return WorkspaceConnectionOut(
        **view.model_dump(),
        account_name=account.name,
        credentials_available=credential_storage_available(),
    )

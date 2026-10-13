"""Account-owned catalog imports with write-only, origin-bound credentials."""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from devfeed_core.models import (
    PartnerAccount,
    PartnerConnection,
    PartnerMembership,
    PartnerPipelineJob,
    utcnow,
)
from devfeed_core.partner_catalog import lock_catalog
from devfeed_core.partner_connections import (
    ConnectionOut,
    configure_connection,
    connection_views,
    update_connection_definition,
)
from devfeed_core.partner_connectors import ConnectorConfig, connector_snapshot
from devfeed_core.partner_credentials import (
    CredentialUnavailable,
    connection_credential,
    credential_storage_available,
    encrypt_credential,
)
from devfeed_core.partner_tools import ShortText
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.schemas import InputModel, ORMModel
from devfeed_core.text_search import text_search
from devfeed_http.pagination import Listing, Page, paginate
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from redis.exceptions import RedisError
from sqlalchemy import func, select

from devfeed_partner_api.auth import Partner, actor, require_partner
from devfeed_partner_api.dependencies import DB, get_redis
from devfeed_partner_api.portal import account_access, visible_accounts

router = APIRouter(
    prefix="/v1/partner/accounts/{account_id}/connections",
    tags=["partner-connections"],
    dependencies=[Depends(require_partner)],
)
logger = logging.getLogger(__name__)
MAX_CONNECTIONS = 25


def validate_partner_connector(config: ConnectorConfig) -> ConnectorConfig:
    if config.auth.secret_ref or (
        config.auth.mode != "none" and config.auth.source != "connection"
    ):
        raise ValueError("Partner imports cannot use server-managed credential references")
    if urlsplit(config.list_path).query or (
        config.detail_path and urlsplit(config.detail_path).query
    ):
        raise ValueError(
            "Put API parameters in the query parameter fields and credentials in authentication"
        )
    return config


class PartnerConnectorConfig(ConnectorConfig):
    requests_per_minute: int = Field(default=60, ge=1, le=60)
    timeout_seconds: int = Field(default=15, ge=1, le=15)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=2_000_000)
    max_pages: int = Field(default=100, ge=1, le=100)


class PartnerConnectionCreate(InputModel):
    name: ShortText
    connector: PartnerConnectorConfig
    credential: SecretStr | None = Field(default=None, json_schema_extra={"writeOnly": True})
    sync_interval_minutes: int = Field(default=360, ge=15, le=10080, strict=True)
    enabled: bool = True

    _connector = field_validator("connector")(validate_partner_connector)


class PartnerConnectionUpdate(InputModel):
    name: ShortText | None = None
    connector: PartnerConnectorConfig | None = None
    credential: SecretStr | None = Field(default=None, json_schema_extra={"writeOnly": True})
    sync_interval_minutes: int | None = Field(default=None, ge=15, le=10080, strict=True)
    enabled: bool
    expected_revision: int = Field(ge=1, strict=True)

    @field_validator("connector")
    @classmethod
    def supported_connector(cls, value):
        return validate_partner_connector(value) if value is not None else None


class PartnerConnectionAction(InputModel):
    action: Literal["sync", "pause", "resume"]
    expected_revision: int = Field(ge=1, strict=True)


class PartnerConnectionDelete(InputModel):
    expected_revision: int = Field(ge=1, strict=True)


class PartnerConnectionOut(ConnectionOut):
    auth_mode: Literal["none", "bearer", "api_key"]
    credential_header: str
    credential_configured: bool
    credentials_managed: bool
    connector_editable: bool


class PartnerConnectionsOut(Page[PartnerConnectionOut]):
    credentials_available: bool


class PartnerConnectionJobOut(ORMModel):
    id: uuid.UUID
    operation: Literal["sync", "sync_product", "assess"]
    status: str
    attempts: int
    created_at: datetime
    finished_at: datetime | None
    error: str | None


class PartnerConnectionJobsOut(Page[PartnerConnectionJobOut]):
    pass


def partner_connection_views(session, connections):
    """Keep global secret references and upstream diagnostics outside the portal."""
    result = []
    for connection, view in zip(connections, connection_views(session, connections), strict=True):
        config = ConnectorConfig.model_validate(view.connector) if view.connector else None
        managed = bool(
            config and config.auth.mode != "none" and config.auth.source == "environment"
        )
        values = view.model_dump()
        values["connector"] = None if managed else config
        if config:
            endpoint = urlsplit(config.list_path)
            values["api_url"] = config.base_url + endpoint.path
            if not managed and config.auth.mode == "none":
                config = config.model_copy(
                    update={"auth": config.auth.model_copy(update={"secret_ref": ""})}
                )
                values["connector"] = config
        values["error"] = (
            "The latest import failed. Review the connection settings and try again."
            if connection.last_error
            else None
        )
        result.append(
            PartnerConnectionOut(
                **values,
                auth_mode=config.auth.mode if config else "none",
                credential_header=config.auth.header if config else "X-API-Key",
                credential_configured=managed or bool(connection.encrypted_credential),
                credentials_managed=managed,
                connector_editable=not managed,
            )
        )
    return result


def connection_access(session, account_id, provider):
    connection = session.scalar(
        select(PartnerConnection).where(
            PartnerConnection.provider == provider,
            PartnerConnection.account_id == account_id,
            PartnerConnection.archived_at.is_(None),
        )
    )
    if connection is None:
        raise HTTPException(404, "Import connection not found")
    return connection


def write_account(session, partner, account_id, *, creating=False):
    account_access(session, partner, account_id)
    caller = hashlib.sha256(f"{partner.issuer}\0{partner.subject}".encode()).hexdigest()
    budgets = [
        RateLimitBudget(key=f"partner-import-caller:{caller}", limit=30, window=60),
        RateLimitBudget(key=f"partner-import-account:{account_id}", limit=30, window=60),
    ]
    if creating:
        budgets.append(
            RateLimitBudget(key=f"partner-import-create:{account_id}", limit=10, window=3600)
        )
    try:
        retry = consume_rate_limits(get_redis(), budgets)
    except RedisError:
        raise HTTPException(503, "Import management is temporarily unavailable") from None
    if retry:
        raise HTTPException(
            429, "Too many import changes; try again shortly", headers={"Retry-After": str(retry)}
        )
    # Match worker lock order. Hold the membership row until commit so a completed
    # revocation cannot race a successful write authenticated against an old snapshot.
    lock_catalog(session)
    account = session.scalar(
        visible_accounts(partner).where(PartnerAccount.id == account_id).with_for_update()
    )
    member = session.scalar(
        select(PartnerMembership)
        .where(
            PartnerMembership.account_id == account_id,
            PartnerMembership.issuer == partner.issuer,
            PartnerMembership.subject == partner.subject,
        )
        .with_for_update(read=True)
    )
    if account is None or member is None:
        raise HTTPException(404, "Partner account not found")
    return account


def check_revision(connection, expected):
    if connection.revision != expected:
        raise HTTPException(409, "Import connection changed; refresh to load the latest settings")


def safe_credential_error(exc):
    return HTTPException(503 if not credential_storage_available() else 422, str(exc))


def log_change(event, connection, partner):
    logger.info(
        event,
        extra={
            "provider": connection.provider,
            "account_id": str(connection.account_id),
            "actor_subject": partner.subject,
            "connection_revision": connection.revision,
        },
    )


@router.get("", response_model=PartnerConnectionsOut, operation_id="partnerConnectionsList")
def list_connections(account_id: uuid.UUID, session: DB, partner: Partner, query: Listing):
    account_access(session, partner, account_id)
    statement = select(PartnerConnection).where(
        PartnerConnection.account_id == account_id, PartnerConnection.archived_at.is_(None)
    )
    if query.q:
        statement = statement.where(
            text_search(query.q, PartnerConnection.provider, PartnerConnection.name)
        )
    page = paginate(
        session,
        statement,
        query,
        {"name": PartnerConnection.name, "provider": PartnerConnection.provider},
        default="name",
    )
    page["items"] = partner_connection_views(session, page["items"])
    return {**page, "credentials_available": credential_storage_available()}


@router.get("/{provider}", response_model=PartnerConnectionOut, operation_id="partnerConnectionGet")
def get_connection(account_id: uuid.UUID, provider: str, session: DB, partner: Partner):
    account_access(session, partner, account_id)
    return partner_connection_views(session, [connection_access(session, account_id, provider)])[0]


@router.post(
    "", response_model=PartnerConnectionOut, status_code=201, operation_id="partnerConnectionCreate"
)
def create_connection(
    account_id: uuid.UUID, body: PartnerConnectionCreate, session: DB, partner: Partner
):
    write_account(session, partner, account_id, creating=True)
    if (
        session.scalar(
            select(func.count())
            .select_from(PartnerConnection)
            .where(
                PartnerConnection.account_id == account_id,
                PartnerConnection.archived_at.is_(None),
            )
        )
        or 0
    ) >= MAX_CONNECTIONS:
        raise HTTPException(409, "This account has reached its 25 import connection limit")
    connection = PartnerConnection(
        account_id=account_id,
        provider=f"account-{account_id.hex}-{uuid.uuid4().hex}",
        name=body.name,
        connector=body.connector.model_dump(mode="json"),
        enabled=False,
        sync_interval_minutes=body.sync_interval_minutes,
        updated_by=actor(partner),
    )
    if body.connector.auth.mode != "none":
        if body.credential is None:
            raise HTTPException(422, "Provide an import API credential")
        try:
            connection.encrypted_credential = encrypt_credential(
                connection, body.connector, body.credential.get_secret_value()
            )
        except CredentialUnavailable as exc:
            raise safe_credential_error(exc) from None
    elif body.credential is not None:
        raise HTTPException(422, "Select authentication before providing a credential")
    session.add(connection)
    session.flush()
    configure_connection(session, connection, body.enabled)
    session.commit()
    log_change("partner_import_added", connection, partner)
    return partner_connection_views(session, [connection])[0]


@router.put(
    "/{provider}", response_model=PartnerConnectionOut, operation_id="partnerConnectionUpdate"
)
def update_connection(
    account_id: uuid.UUID,
    provider: str,
    body: PartnerConnectionUpdate,
    session: DB,
    partner: Partner,
):
    write_account(session, partner, account_id)
    connection = connection_access(session, account_id, provider)
    check_revision(connection, body.expected_revision)
    current = ConnectorConfig.model_validate(connector_snapshot(connection))
    managed = current.auth.mode != "none" and current.auth.source == "environment"
    if managed and (body.connector is not None or body.credential is not None):
        raise HTTPException(403, "This connection's authentication is managed by an administrator")
    config = body.connector or current
    credential_changed = False
    if not managed:
        if config.auth.mode == "none":
            if body.credential is not None:
                raise HTTPException(422, "Select authentication before providing a credential")
            credential_changed = bool(connection.encrypted_credential)
            connection.encrypted_credential = None
        elif body.credential is not None:
            try:
                connection.encrypted_credential = encrypt_credential(
                    connection, config, body.credential.get_secret_value()
                )
            except CredentialUnavailable as exc:
                raise safe_credential_error(exc) from None
            credential_changed = True
        else:
            try:
                connection_credential(connection, config)
            except CredentialUnavailable as exc:
                raise safe_credential_error(exc) from None
    changed = update_connection_definition(
        session, connection, config, credential_changed=credential_changed
    )
    if body.name is not None and body.name != connection.name:
        connection.name = body.name
        connection.revision += 1
    configure_connection(
        session,
        connection,
        body.enabled,
        sync=changed,
        sync_interval_minutes=body.sync_interval_minutes,
    )
    connection.updated_by = actor(partner)
    session.commit()
    log_change("partner_import_updated", connection, partner)
    return partner_connection_views(session, [connection])[0]


@router.post(
    "/{provider}/actions",
    response_model=PartnerConnectionOut,
    operation_id="partnerConnectionAction",
)
def connection_action(
    account_id: uuid.UUID,
    provider: str,
    body: PartnerConnectionAction,
    session: DB,
    partner: Partner,
):
    write_account(session, partner, account_id)
    connection = connection_access(session, account_id, provider)
    check_revision(connection, body.expected_revision)
    if body.action == "sync" and not connection.enabled:
        raise HTTPException(409, "Resume this import connection before syncing")
    if body.action != "pause":
        try:
            connection_credential(connection)
        except CredentialUnavailable as exc:
            raise safe_credential_error(exc) from None
    configure_connection(session, connection, body.action != "pause", sync=body.action != "pause")
    connection.updated_by = actor(partner)
    session.commit()
    log_change("partner_import_" + body.action, connection, partner)
    return partner_connection_views(session, [connection])[0]


@router.delete(
    "/{provider}", response_model=PartnerConnectionOut, operation_id="partnerConnectionDelete"
)
def disconnect_connection(
    account_id: uuid.UUID,
    provider: str,
    body: PartnerConnectionDelete,
    session: DB,
    partner: Partner,
):
    """Archive the connection and erase its token, retaining catalog and reporting history."""
    write_account(session, partner, account_id)
    connection = connection_access(session, account_id, provider)
    check_revision(connection, body.expected_revision)
    connection.archived_at = utcnow()
    if connection.encrypted_credential:
        connection.encrypted_credential = None
        connection.revision += 1
        connection.sync_revision += 1
    configure_connection(session, connection, False)
    connection.updated_by = actor(partner)
    session.commit()
    log_change("partner_import_disconnected", connection, partner)
    return partner_connection_views(session, [connection])[0]


@router.get(
    "/{provider}/jobs",
    response_model=PartnerConnectionJobsOut,
    operation_id="partnerConnectionJobs",
)
def connection_jobs(
    account_id: uuid.UUID, provider: str, session: DB, partner: Partner, query: Listing
):
    account_access(session, partner, account_id)
    connection_access(session, account_id, provider)
    statement = select(PartnerPipelineJob).where(PartnerPipelineJob.provider == provider)
    if query.q:
        statement = statement.where(PartnerPipelineJob.status.icontains(query.q, autoescape=True))
    page = paginate(
        session,
        statement,
        query,
        {"created_at": PartnerPipelineJob.created_at, "status": PartnerPipelineJob.status},
        "-created_at",
    )
    page["items"] = [
        PartnerConnectionJobOut(
            id=row.id,
            operation=row.operation,
            status=row.status,
            attempts=row.attempts,
            created_at=row.created_at,
            finished_at=row.finished_at,
            error="Import did not complete. Review the connection settings and retry."
            if row.error
            else None,
        )
        for row in page["items"]
    ]
    return page

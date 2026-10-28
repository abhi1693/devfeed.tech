"""Session-bound subscriber gateway. Chimely credentials never reach the browser."""

from __future__ import annotations

import uuid

from devfeed_core.job_retries import retry_notification
from devfeed_core.notifications import notification_subscriber_id
from devfeed_http.inbox import InboxProxy, InboxRouter, InboxSession
from devfeed_http.operation_policies import GatewayPolicy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin, AdminIdentity
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view

router = APIRouter(prefix="/v1/admin/notifications", tags=["admin-notifications"])


class NotificationConfig(BaseModel):
    enabled: bool
    environment: str | None = None
    subscriber_id: str | None = None


def subscriber_id(admin: AdminIdentity) -> str:
    return notification_subscriber_id(
        audience="admin",
        issuer=admin.issuer,
        subject=admin.subject,
        organization_id=admin.organization_id,
    )


@router.get("/config", response_model=NotificationConfig, operation_id="admin_notification_config")
def config(admin: Admin) -> NotificationConfig:
    settings = get_settings()
    enabled = settings.notifications_enabled and bool(settings.chimely_admin_environment)
    return NotificationConfig(
        enabled=enabled,
        environment=settings.chimely_admin_environment if enabled else None,
        subscriber_id=subscriber_id(admin) if enabled else None,
    )


@router.post(
    "/deliveries/{identifier}/retry",
    response_model=AdminJobOut,
    operation_id="admin_notification_retry",
)
def retry(identifier: uuid.UUID, admin: Admin, session: DB) -> AdminJobOut:
    job = retry_notification(session, identifier)
    session.commit()
    return job_view(job, "notifications")


async def resolve_inbox_session(admin: Admin) -> InboxSession:
    # Settings initialization can read environment files on the first request.
    settings = await run_in_threadpool(get_settings)
    if not settings.notifications_enabled or not settings.chimely_admin_environment:
        raise HTTPException(503, "Admin notifications are not enabled")
    assert settings.chimely_api_url and settings.chimely_admin_hmac_secret
    return InboxSession(
        api_url=settings.chimely_api_url,
        environment=settings.chimely_admin_environment,
        secret=settings.chimely_admin_hmac_secret.get_secret_value(),
        subscriber_id=subscriber_id(admin),
        expires_at=admin.expires_at,
        client_factory=InboxProxy.http_client,
    )


router.include_router(
    InboxRouter(
        session_dependency=resolve_inbox_session,
        operation_prefix="adminInbox",
        gateway_policy=GatewayPolicy(),
    )
)

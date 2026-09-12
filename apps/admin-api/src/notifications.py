"""Session-bound subscriber gateway. Chimely credentials never reach the browser."""

import uuid

from devfeed_core.job_retries import retry_notification
from devfeed_core.notifications import notification_subscriber_id
from devfeed_http.inbox import http_client, proxy_inbox, subscriber_headers
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view

router = APIRouter(prefix="/v1/admin/notifications", tags=["admin-notifications"])


class NotificationConfig(BaseModel):
    enabled: bool
    environment: str | None = None
    subscriber_id: str | None = None


def subscriber_id(admin):
    return notification_subscriber_id(
        audience="admin",
        issuer=admin.issuer,
        subject=admin.subject,
        organization_id=admin.organization_id,
    )


@router.get("/config", response_model=NotificationConfig, operation_id="admin_notification_config")
def config(admin: Admin):
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
def retry(identifier: uuid.UUID, admin: Admin, session: DB):
    job = retry_notification(session, identifier)
    session.commit()
    return job_view(job, "notifications")


@router.api_route(
    "/chimely/v1/inbox/{path:path}", methods=["GET", "POST", "PUT"], include_in_schema=False
)
async def inbox_proxy(path: str, request: Request, admin: Admin):
    # Settings initialization can read environment files on the first request.
    settings = await run_in_threadpool(get_settings)
    if not settings.notifications_enabled or not settings.chimely_admin_environment:
        raise HTTPException(503, "Admin notifications are not enabled")
    assert settings.chimely_api_url and settings.chimely_admin_hmac_secret
    return await proxy_inbox(
        path,
        request,
        api_url=settings.chimely_api_url,
        headers=subscriber_headers(
            settings.chimely_admin_environment,
            settings.chimely_admin_hmac_secret.get_secret_value(),
            subscriber_id(admin),
        ),
        expires_at=admin.expires_at,
        client_factory=http_client,
    )

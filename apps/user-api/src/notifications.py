"""Only the signed-in user's Chimely inbox is accessible through this gateway."""

from __future__ import annotations

from devfeed_core.notifications import notification_subscriber_id
from devfeed_http.inbox import InboxProxy, InboxRouter, InboxSession
from devfeed_http.operation_policies import ExtensionTransportPolicy, ReaderGatewayPolicy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from devfeed_user_api.auth import BrowserUser, User, UserIdentity
from devfeed_user_api.notification_config import get_settings

router = APIRouter(prefix="/v1/user/notifications", tags=["user-notifications"])


class NotificationConfig(BaseModel):
    enabled: bool
    environment: str | None = None
    subscriber_id: str | None = None


def subscriber_id(user: UserIdentity) -> str:
    return notification_subscriber_id(
        audience="user",
        issuer=user.issuer,
        subject=user.subject,
        organization_id=user.organization_id,
    )


@router.get("/config", response_model=NotificationConfig)
def config(user: User) -> NotificationConfig:
    settings = get_settings()
    enabled = settings.notifications_enabled and bool(settings.chimely_user_environment)
    return NotificationConfig(
        enabled=enabled,
        environment=settings.chimely_user_environment if enabled else None,
        subscriber_id=subscriber_id(user) if enabled else None,
    )


async def resolve_inbox_session(user: BrowserUser) -> InboxSession:
    # Widget authorization headers belong to its upstream protocol. Authenticate
    # the gateway with the reader session and retain its CSRF protections.
    # Settings initialization can read environment files on the first request.
    settings = await run_in_threadpool(get_settings)
    if not settings.notifications_enabled or not settings.chimely_user_environment:
        raise HTTPException(503, "User notifications are not enabled")
    assert settings.chimely_api_url and settings.chimely_user_hmac_secret
    return InboxSession(
        api_url=settings.chimely_api_url,
        environment=settings.chimely_user_environment,
        secret=settings.chimely_user_hmac_secret.get_secret_value(),
        subscriber_id=subscriber_id(user),
        expires_at=user.expires_at,
        client_factory=InboxProxy.http_client,
    )


router.include_router(
    InboxRouter(
        session_dependency=resolve_inbox_session,
        operation_prefix="userInbox",
        gateway_policy=ReaderGatewayPolicy(extension=ExtensionTransportPolicy()),
    )
)

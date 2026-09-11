"""Only the signed-in user's Chimely inbox is accessible through this gateway."""

from devfeed_core.notifications import notification_subscriber_id
from devfeed_http.inbox import http_client, proxy_inbox, subscriber_headers
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from devfeed_user_api.auth import User
from devfeed_user_api.notification_config import get_settings

router = APIRouter(prefix="/v1/user/notifications", tags=["user-notifications"])


class NotificationConfig(BaseModel):
    enabled: bool
    environment: str | None = None
    subscriber_id: str | None = None


def subscriber_id(user):
    return notification_subscriber_id(
        audience="user",
        issuer=user.issuer,
        subject=user.subject,
        organization_id=user.organization_id,
    )


@router.get("/config", response_model=NotificationConfig)
def config(user: User):
    settings = get_settings()
    enabled = settings.notifications_enabled and bool(settings.chimely_user_environment)
    return NotificationConfig(
        enabled=enabled,
        environment=settings.chimely_user_environment if enabled else None,
        subscriber_id=subscriber_id(user) if enabled else None,
    )


@router.api_route(
    "/chimely/v1/inbox/{path:path}", methods=["GET", "POST", "PUT"], include_in_schema=False
)
async def inbox_proxy(path: str, request: Request, user: User):
    settings = get_settings()
    if not settings.notifications_enabled or not settings.chimely_user_environment:
        raise HTTPException(503, "User notifications are not enabled")
    assert settings.chimely_api_url and settings.chimely_user_hmac_secret
    return await proxy_inbox(
        path,
        request,
        api_url=settings.chimely_api_url,
        headers=subscriber_headers(
            settings.chimely_user_environment,
            settings.chimely_user_hmac_secret.get_secret_value(),
            subscriber_id(user),
        ),
        expires_at=user.expires_at,
        client_factory=http_client,
    )

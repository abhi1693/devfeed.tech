"""Session-bound subscriber gateway. Chimely credentials never reach the browser."""

import hashlib
import hmac
import json
import re
import time
import uuid

import anyio
import httpx
from devfeed_core.job_retries import retry_notification
from devfeed_core.notifications import notification_subscriber_id
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.config import get_settings
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.jobs import AdminJobOut, job_view

router = APIRouter(prefix="/v1/admin/notifications", tags=["admin-notifications"])
MAX_BODY = 16_384
MAX_RESPONSE = 2_000_000
STREAM_SECONDS = 25
ITEM_ACTION = re.compile(
    r"(?:notifications/notif|broadcasts/bcast)_[a-z0-9]{26}/(?:read|unread|archive|unarchive)\Z"
)


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


def allowed_path(method, path):
    return (
        method == "GET"
        and path in {"items", "counts", "preferences", "stream"}
        or method == "PUT"
        and path == "preferences"
        or method == "POST"
        and (
            path in {"read-all", "seen-all", "archive-all", "archive-read"}
            or ITEM_ACTION.fullmatch(path) is not None
        )
    )


def subscriber_headers(admin):
    settings = get_settings()
    assert settings.chimely_admin_hmac_secret and settings.chimely_admin_environment
    identifier = subscriber_id(admin)
    signature = hmac.new(
        settings.chimely_admin_hmac_secret.get_secret_value().encode(),
        identifier.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Chimely-Environment": settings.chimely_admin_environment,
        "X-Chimely-Subscriber": identifier,
        "X-Chimely-Subscriber-Hash": signature,
    }


def proxy_error(status):
    # A Chimely 401 is a backend configuration failure, not an expired admin session.
    code = status if status in {400, 404, 409, 422, 429} else 503
    return Response(
        json.dumps(
            {
                "error": {
                    "code": "notification_service_unavailable"
                    if code == 503
                    else "invalid_request",
                    "message": "Notifications are temporarily unavailable. Please retry.",
                }
            }
        ),
        status_code=code,
        media_type="application/json",
    )


def http_client():
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=3, write=5, pool=3),
        follow_redirects=False,
        trust_env=False,
    )


@router.api_route(
    "/chimely/v1/inbox/{path:path}", methods=["GET", "POST", "PUT"], include_in_schema=False
)
async def inbox_proxy(path: str, request: Request, admin: Admin):
    if not allowed_path(request.method, path):
        raise HTTPException(404, "Not found")
    settings = get_settings()
    if not settings.notifications_enabled or not settings.chimely_admin_environment:
        raise HTTPException(503, "Admin notifications are not enabled")
    # Deliberately ignore all client-supplied Chimely identities/hashes and API keys.
    params = {}
    for key in ("limit", "cursor", "filter", "last_event_id"):
        values = request.query_params.getlist(key)
        if len(values) > 1 or values and len(values[0]) > 2048:
            raise HTTPException(422, "Invalid inbox query")
        if values:
            params[key] = values[0]
    headers = subscriber_headers(admin)
    for key in ("if-none-match", "last-event-id"):
        if value := request.headers.get(key):
            if len(value) > 2048:
                raise HTTPException(422, "Invalid inbox header")
            headers[key] = value
    if "last_event_id" in params:
        headers["last-event-id"] = params.pop("last_event_id")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413, "Inbox request is too large")
    if body:
        try:
            json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Invalid inbox request") from None
        headers["content-type"] = "application/json"
    client = http_client()
    upstream = None
    handed_off = False
    try:
        assert settings.chimely_api_url
        upstream = await client.send(
            client.build_request(
                request.method,
                settings.chimely_api_url.rstrip("/") + "/v1/inbox/" + path,
                params=params,
                headers=headers,
                content=bytes(body),
            ),
            stream=True,
        )
        result_headers = {"Cache-Control": "no-store"}
        if etag := upstream.headers.get("etag"):
            result_headers["ETag"] = etag
        if upstream.status_code == 304:
            return Response(status_code=304, headers=result_headers)
        if not upstream.is_success:
            return proxy_error(upstream.status_code)
        if path == "stream":
            if not upstream.headers.get("content-type", "").startswith("text/event-stream"):
                return proxy_error(503)

            async def chunks():
                try:
                    # Bounded connections re-check the real admin session at
                    # least every 25s; a revoked browser never holds a reusable HMAC.
                    with anyio.move_on_after(
                        min(STREAM_SECONDS, max(0, admin.expires_at - time.time()))
                    ):
                        # Flush the response through the web gateway immediately.
                        # Chimely's default first heartbeat (30s) arrives after
                        # our bounded session stream has already closed (25s).
                        yield b": connected\n\n"
                        async for chunk in upstream.aiter_bytes():
                            yield chunk
                except httpx.HTTPError:
                    return  # EventSource reconnects; REST remains authoritative.
                finally:
                    with anyio.CancelScope(shield=True):
                        await upstream.aclose()
                        await client.aclose()

            handed_off = True
            return StreamingResponse(
                chunks(),
                media_type="text/event-stream",
                headers={**result_headers, "X-Accel-Buffering": "no"},
            )
        content = bytearray()
        async for chunk in upstream.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_RESPONSE:
                return proxy_error(503)
        return Response(
            bytes(content),
            status_code=upstream.status_code,
            media_type="application/json",
            headers=result_headers,
        )
    except httpx.HTTPError:
        return proxy_error(503)
    finally:
        if not handed_off:
            with anyio.CancelScope(shield=True):
                if upstream is not None:
                    await upstream.aclose()
                await client.aclose()

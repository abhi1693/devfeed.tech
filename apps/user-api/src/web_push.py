"""Browser-owned consent for the single daily personalized Must Read alert."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

from devfeed_core.db import session_factory
from devfeed_core.models import UserAccount, WebPushSubscription, utcnow
from devfeed_core.web_push import (
    USER_PUSH_POLICY_KEY,
    endpoint_digest,
    get_web_push_settings,
    next_push_datetime,
    validate_subscription,
)
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from devfeed_user_api import auth, oidc
from devfeed_user_api.config import get_settings as user_settings
from devfeed_user_api.dependencies import DB

router = APIRouter(prefix="/v1/user/notifications/push", tags=["user-notifications"])


def publish_session_policy():
    """Workers receive a fingerprint, never the user API's OIDC credentials."""
    auth.get_redis().set(USER_PUSH_POLICY_KEY, oidc.policy_key(user_settings()))


class PushConfig(BaseModel):
    enabled: bool
    public_key: str | None
    delivery_hour: int
    delivery_timezone: str | None


class SubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    p256dh: str = Field(min_length=1, max_length=100)
    auth: str = Field(min_length=1, max_length=100)


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: SubscriptionKeys
    timezone: str = Field(min_length=1, max_length=100)


class SubscriptionState(BaseModel):
    id: uuid.UUID
    consent_id: uuid.UUID
    enabled: bool
    timezone: str


class SubscriptionSummary(SubscriptionState):
    endpoint_hash: str


class Subscriptions(BaseModel):
    subscriptions: list[SubscriptionSummary]


@router.get("/config", response_model=PushConfig)
def config(user: auth.BrowserUser, session: DB):
    settings = get_web_push_settings()
    timezone = session.scalar(
        select(WebPushSubscription.timezone)
        .where(
            WebPushSubscription.user_id == uuid.UUID(user.user_id),
            WebPushSubscription.enabled.is_(True),
            WebPushSubscription.authorization_expires_at > utcnow(),
        )
        .order_by(WebPushSubscription.created_at, WebPushSubscription.id)
        .limit(1)
    )
    return PushConfig(
        enabled=settings.web_push_enabled,
        public_key=settings.web_push_public_key if settings.web_push_enabled else None,
        delivery_hour=settings.web_push_delivery_hour,
        delivery_timezone=timezone,
    )


@router.get("/subscriptions", response_model=Subscriptions)
def subscriptions(user: auth.BrowserUser, session: DB):
    rows = session.scalars(
        select(WebPushSubscription)
        .where(WebPushSubscription.user_id == uuid.UUID(user.user_id))
        .order_by(WebPushSubscription.created_at)
    )
    now = utcnow()
    return Subscriptions(
        subscriptions=[
            SubscriptionSummary(
                id=row.id,
                consent_id=row.consent_id,
                endpoint_hash=row.endpoint_hash,
                enabled=row.enabled and row.authorization_expires_at > now,
                timezone=row.timezone,
            )
            for row in rows
        ]
    )


def browser_authorization(request: Request, user):
    token = request.cookies.get(oidc.cookie_name(user_settings(), "session"), "")
    if not auth.TOKEN.fullmatch(token):
        raise HTTPException(401, "User sign-in required")
    try:
        raw = auth.get_redis().get(auth.key("session", token))
    except RedisError as exc:
        raise HTTPException(503, "User sessions temporarily unavailable") from exc
    try:
        record = json.loads(raw) if raw else {}
        expiry = datetime.fromtimestamp(record["absolute_expires_at"], UTC)
        if record["user_id"] != user.user_id or expiry <= utcnow():
            raise ValueError("Invalid session")
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(401, "User session expired") from exc
    return hashlib.sha256(token.encode()).hexdigest(), expiry


@router.post("/subscriptions", response_model=SubscriptionState)
def subscribe(payload: SubscriptionInput, request: Request, user: auth.BrowserUser, session: DB):
    settings = get_web_push_settings()
    if not settings.web_push_enabled:
        raise HTTPException(503, "Daily browser notifications are not configured")
    try:
        validate_subscription(
            payload.endpoint, payload.keys.p256dh, payload.keys.auth, payload.timezone
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Invalid browser push subscription") from exc
    session_hash, expires_at = browser_authorization(request, user)
    user_id = uuid.UUID(user.user_id)
    account = session.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
    if account is None:
        raise HTTPException(401, "User account unavailable")
    digest = endpoint_digest(payload.endpoint)
    row = session.scalar(
        select(WebPushSubscription)
        .where(WebPushSubscription.endpoint_hash == digest)
        .with_for_update()
    )
    if row is not None and (
        not hmac.compare_digest(row.p256dh, payload.keys.p256dh)
        or not hmac.compare_digest(row.auth, payload.keys.auth)
    ):
        raise HTTPException(409, "Browser subscription changed; try enabling it again")
    reschedule = (
        row is None
        or not row.enabled
        or row.user_id != user_id
        or row.session_hash != session_hash
        or row.timezone != payload.timezone
        or row.allowed_kinds != ["daily_must_read"]
    )
    if row is None or row.user_id != user_id or not row.enabled:
        active = session.scalar(
            select(func.count())
            .select_from(WebPushSubscription)
            .where(
                WebPushSubscription.user_id == user_id,
                WebPushSubscription.enabled.is_(True),
                WebPushSubscription.authorization_expires_at > utcnow(),
            )
        )
        if active is not None and active >= 10:
            raise HTTPException(409, "Daily notifications are already enabled on ten browsers")
    now = utcnow()
    if row is None:
        row = WebPushSubscription(endpoint=payload.endpoint, endpoint_hash=digest)
        session.add(row)
    elif row.user_id != user_id:
        row.created_at = now
    # Repeated registration is idempotent; rebinding never preserves another
    # account's ownership. The sender also checks event/subscription ownership.
    row.user_id = user_id
    row.p256dh = payload.keys.p256dh
    row.auth = payload.keys.auth
    row.timezone = payload.timezone
    row.enabled = True
    # The current settings promise grants this type only. Future kinds require
    # an explicit consent flow and cannot inherit a browser permission grant.
    row.allowed_kinds = ["daily_must_read"]
    row.session_hash = session_hash
    row.authorization_expires_at = expires_at
    row.updated_at = now
    if reschedule:
        row.consent_id = uuid.uuid4()
        # Audience snapshots must not recruit a later consent period, even when
        # the browser keeps the same endpoint and account/session.
        row.created_at = now
        row.next_push_at = next_push_datetime(
            now, payload.timezone, settings.web_push_delivery_hour
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Browser subscription changed; try enabling it again") from exc
    return SubscriptionState(
        id=row.id, consent_id=row.consent_id, enabled=row.enabled, timezone=row.timezone
    )


@router.delete("/subscriptions/{identifier}", status_code=204, response_class=Response)
def unsubscribe(identifier: uuid.UUID, user: auth.BrowserUser, session: DB):
    row = session.scalar(
        select(WebPushSubscription)
        .where(
            WebPushSubscription.id == identifier,
            WebPushSubscription.user_id == uuid.UUID(user.user_id),
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Browser subscription not found")
    row.enabled = False
    row.updated_at = utcnow()
    session.commit()
    return Response(status_code=204)


def revoke_browser_push(token: str):
    """Stop this session's future deliveries before logout or account switching."""
    if not get_web_push_settings().web_push_enabled:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    with session_factory().begin() as session:
        session.execute(
            update(WebPushSubscription)
            .where(WebPushSubscription.session_hash == digest)
            .values(enabled=False, updated_at=utcnow())
        )

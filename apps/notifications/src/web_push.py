"""Encrypted delivery of consented notification types through browser push relays."""

import base64
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlsplit

import httpcore
from cryptography.hazmat.primitives import serialization
from devfeed_core.config import get_settings as core_settings
from devfeed_core.db import session_factory
from devfeed_core.feeds.fetcher import FeedError, PublicNetworkBackend
from devfeed_core.job_lifecycle import clear_lease, start_job
from devfeed_core.jobs import LEASE_SECONDS
from devfeed_core.logging import log_context
from devfeed_core.models import (
    WebPushDelivery,
    WebPushEvent,
    WebPushSubscription,
    utcnow,
)
from devfeed_core.push_audience import PushAudience, audience_contains
from devfeed_core.push_types import WebPushMessage, get_push_type
from devfeed_core.redis import create_redis
from devfeed_core.web_push import (
    USER_PUSH_POLICY_KEY,
    PublicWebPushSettings,
    decode_key,
    retry_push,
    validate_endpoint,
)
from py_vapid import Vapid02
from pydantic import SecretStr, field_validator, model_validator
from pywebpush import WebPusher, WebPushException
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import select

logger = logging.getLogger(__name__)


class Settings(PublicWebPushSettings):
    web_push_private_key: SecretStr | None = None
    web_push_subject: str | None = None
    web_push_site_url: str = "https://devfeed.tech"

    @field_validator("web_push_private_key", "web_push_subject", mode="before")
    @classmethod
    def empty_secret(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def valid_sender(self):
        if not self.web_push_enabled:
            return self
        secret = self.web_push_private_key
        if not secret:
            raise ValueError("Push delivery requires a VAPID private key")
        try:
            decode_key(secret.get_secret_value(), 32)
            vapid = Vapid02.from_string(secret.get_secret_value())
            public = vapid.public_key.public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
            if public != decode_key(self.web_push_public_key, 65):
                raise ValueError("Mismatched VAPID keys")
        except Exception as exc:
            raise ValueError("Push delivery requires matching P-256 VAPID keys") from exc
        subject = urlsplit(self.web_push_subject or "")
        if (
            subject.scheme not in {"mailto", "https"}
            or (subject.scheme == "https" and not subject.hostname)
            or (subject.scheme == "mailto" and "@" not in subject.path)
            or any(char.isspace() for char in self.web_push_subject or "")
        ):
            raise ValueError("Push delivery requires a mailto or HTTPS contact subject")
        site = urlsplit(self.web_push_site_url)
        if (
            site.scheme != "https"
            or not site.hostname
            or site.username
            or site.password
            or site.path not in {"", "/"}
            or site.query
            or site.fragment
        ):
            raise ValueError("Push delivery requires an HTTPS reader origin")
        return self


@lru_cache
def get_settings():
    return Settings()


def session_authorization_expires_at(subscription, now) -> datetime | None:
    """Return the live idle/absolute deadline only for the account's current policy."""
    with create_redis(
        core_settings(),
        socket_connect_timeout=2,
        socket_timeout=2,
        retry=Retry(NoBackoff(), 0),
    ) as client:
        raw, active_policy = client.mget(
            f"devfeed:user:session:{subscription.session_hash}", USER_PUSH_POLICY_KEY
        )
    if raw is None:
        return None
    if active_policy is None:
        raise RedisError("Browser session policy is temporarily unavailable")
    try:
        record = json.loads(raw)
        if (
            record["user_id"] != str(subscription.user_id)
            or record.get("policy") != active_policy.decode()
        ):
            return None
        expires_at = datetime.fromtimestamp(
            min(record["expires_at"], record["absolute_expires_at"]), UTC
        )
        return expires_at if expires_at > now else None
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        return None


def build_request(
    subscription, event, message: WebPushMessage, settings, now, *, authorized_until=None
):
    """Use maintained encryption/signing libraries without their HTTP adapters."""
    endpoint = validate_endpoint(subscription.endpoint)
    expires_at = min(event.expires_at, subscription.authorization_expires_at)
    if authorized_until is not None:
        expires_at = min(expires_at, authorized_until)
    path = message.action_url
    payload = {
        "notification": {
            "title": message.title,
            "body": message.body,
            "navigate": settings.web_push_site_url.rstrip("/") + path,
            "tag": f"push-{event.id}",
        },
        "data": {
            "kind": message.kind,
            "notification_id": str(event.id),
            "url": path,
            "subscription_id": str(subscription.consent_id),
            "expires_at": int(expires_at.timestamp()),
        },
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > 3500:
        raise ValueError("Push payload exceeds the browser limit")
    encoded = WebPusher(
        {"endpoint": endpoint, "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth}}
    ).encode(data, content_encoding="aes128gcm")
    vapid = Vapid02.from_string(settings.web_push_private_key.get_secret_value())
    origin = urlsplit(endpoint)
    ttl = max(0, int((expires_at - now).total_seconds()))
    headers = vapid.sign(
        {
            "aud": f"https://{origin.hostname}",
            "sub": settings.web_push_subject,
            "exp": int((now + timedelta(hours=12)).timestamp()),
        }
    )
    headers.update(
        {
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": "normal",
            "Topic": base64.urlsafe_b64encode(event.id.bytes).decode().rstrip("="),
        }
    )
    return endpoint, headers, encoded["body"]


def send_request(endpoint, headers, content):
    """DNS is pinned to public IPs; no redirects, proxies, cookies or response body."""
    validate_endpoint(endpoint)
    with (
        httpcore.ConnectionPool(
            network_backend=PublicNetworkBackend(), max_connections=1, max_keepalive_connections=0
        ) as pool,
        pool.stream(
            "POST",
            endpoint,
            headers=headers,
            content=content,
            extensions={"timeout": {"connect": 10, "read": 10, "write": 10, "pool": 10}},
        ) as response,
    ):
        retry_after = next(
            (
                value.decode("ascii", errors="ignore")
                for name, value in response.headers
                if name.lower() == b"retry-after"
            ),
            None,
        )
        return response.status, push_retry_after(retry_after, utcnow())


def push_retry_after(value, now):
    """Keep a relay's minimum retry time intact; the notification expiry bounds it."""
    if not value:
        return 0
    try:
        seconds = int(value)
    except ValueError:
        try:
            seconds = int((parsedate_to_datetime(value) - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds)


def _finish(job, now, error=None):
    job.status, job.finished_at, job.error = "succeeded", now, error
    clear_lease(job)


def deliver_web_push(job_id):
    # Browser endpoints and encryption keys never enter application logs.
    with log_context(service="web-push", job_kind="notifications", job_id=job_id):
        _deliver(uuid.UUID(job_id))


def _deliver(identifier):
    settings = get_settings()
    if not settings.web_push_enabled:
        return
    factory = session_factory()
    with factory.begin() as session:
        job = session.scalar(
            select(WebPushDelivery).where(WebPushDelivery.id == identifier).with_for_update()
        )
        now = utcnow()
        if job is None or job.status != "queued" or job.available_at > now:
            return
        event = session.get(WebPushEvent, job.event_id)
        if event is None or event.expires_at <= now:
            _finish(job, now, "Skipped: browser notification expired")
            return
        token = start_job(job, now, LEASE_SECONDS)
        subscription_id = job.subscription_id
    with factory.begin() as session:
        # Serialize consent revocation and account rebind through network completion.
        subscription = session.scalar(
            select(WebPushSubscription)
            .where(WebPushSubscription.id == subscription_id)
            .with_for_update()
        )
        job = session.scalar(
            select(WebPushDelivery)
            .where(WebPushDelivery.id == identifier, WebPushDelivery.lease_token == token)
            .with_for_update()
        )
        if job is None:
            return
        now = utcnow()
        event = session.get(WebPushEvent, job.event_id)
        if (
            not subscription
            or not event
            or not subscription.enabled
            or subscription.user_id != job.user_id
            or event.kind not in subscription.allowed_kinds
            or subscription.session_hash != job.session_hash
            or subscription.consent_id != job.consent_id
            or subscription.authorization_expires_at <= now
            or subscription.created_at > event.created_at
            or event.expires_at <= now
        ):
            _finish(job, now, "Skipped: browser consent, session or notification expired")
            return
        try:
            authorized_until = session_authorization_expires_at(subscription, now)
        except RedisError:
            retry_push(
                job,
                "User sessions are temporarily unavailable",
                now,
                min(event.expires_at, subscription.authorization_expires_at),
            )
            return
        if authorized_until is None:
            subscription.enabled, subscription.updated_at = False, now
            _finish(job, now, "Skipped: browser session is no longer authorized")
            return
        expires_at = min(event.expires_at, subscription.authorization_expires_at, authorized_until)
        try:
            audience = PushAudience.model_validate(event.audience)
            policy = get_push_type(event.kind)
            stored = WebPushMessage.model_validate(event.payload)
            if stored.kind != event.kind:
                raise ValueError("Notification type changed")
            policy.validate(stored, audience)
            message = (
                policy.prepare(session, event, job.user_id, now)
                if audience_contains(session, audience, job.user_id, event.created_at)
                else None
            )
            if message is not None:
                if message.kind != event.kind:
                    raise ValueError("Notification renderer changed the consented type")
                policy.validate(message, audience)
        except ValueError:
            _finish(job, now, "Skipped: unsupported or invalid notification type")
            return
        if message is None:
            _finish(job, now, "Skipped: notification recipient or content is no longer eligible")
            return
        now = utcnow()
        if expires_at <= now:
            _finish(job, now, "Skipped: browser session or notification expired during preparation")
            return
        try:
            endpoint, headers, content = build_request(
                subscription, event, message, settings, now, authorized_until=authorized_until
            )
            status, retry_after = send_request(endpoint, headers, content)
        except (ValueError, WebPushException):
            subscription.enabled, subscription.updated_at = False, now
            retry_push(job, "Invalid browser subscription", now, expires_at, retryable=False)
            return
        except FeedError:
            retry_push(
                job,
                "Browser relay resolved to a disallowed address",
                now,
                expires_at,
                retryable=False,
            )
            return
        except (httpcore.NetworkError, httpcore.TimeoutException, httpcore.ProtocolError):
            retry_push(job, "Browser relay is temporarily unreachable", utcnow(), expires_at)
            return
        now = utcnow()
        if 200 <= status < 300:
            job.accepted_at = now
            _finish(job, now)
        elif status in {404, 410}:
            subscription.enabled, subscription.updated_at = False, now
            _finish(job, now, "Skipped: browser subscription expired")
        else:
            retry_push(
                job,
                f"Browser relay returned HTTP {status}",
                now,
                expires_at,
                retry_after=retry_after,
                retryable=status in {429, 500, 502, 503, 504},
            )
        logger.info(
            "web_push_attempt_completed",
            extra={"job_status": job.status, "attempt": job.attempts},
        )

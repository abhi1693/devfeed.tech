"""Public Web Push contract and bounded, daily Must Reads outbox scheduling."""

import base64
import hashlib
import re
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select

from devfeed_core.job_lifecycle import clear_lease
from devfeed_core.models import (
    Article,
    DailyMustReadPush,
    UserAccount,
    UserRecommendation,
    WebPushDelivery,
    WebPushSubscription,
    utcnow,
)
from devfeed_core.must_reads import ensure_snapshot, read_snapshot, recommendation_eligibility
from devfeed_core.user_settings import FeedSettings

MAX_PUSH_ATTEMPTS = 4


def decode_key(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("Invalid Web Push key")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise ValueError("Invalid Web Push key") from exc
    if len(decoded) != length:
        raise ValueError("Invalid Web Push key length")
    return decoded


class PublicWebPushSettings(BaseSettings):
    """Reader/scheduler settings contain no VAPID signing credential."""

    model_config = SettingsConfigDict(
        env_prefix="DEVFEED_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    web_push_enabled: bool = False
    web_push_public_key: str | None = None
    web_push_delivery_hour: int = Field(default=9, ge=0, le=23)

    @field_validator("web_push_public_key", mode="before")
    @classmethod
    def empty_key(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def valid_public_key(self):
        if self.web_push_enabled and (
            not self.web_push_public_key or decode_key(self.web_push_public_key, 65)[0] != 4
        ):
            raise ValueError("Web Push requires an uncompressed P-256 public key")
        return self


@lru_cache
def get_web_push_settings():
    return PublicWebPushSettings()


def endpoint_digest(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def validate_endpoint(endpoint):
    """Accept production browser relays only; destination credentials stay opaque."""
    if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 4096:
        raise ValueError("Invalid browser push endpoint")
    if any(ord(char) < 33 or ord(char) > 126 for char in endpoint) or "\\" in endpoint:
        raise ValueError("Invalid browser push endpoint")
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    allowed = host in {
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
    } or bool(re.fullmatch(r"[a-z0-9-]+\.notify\.windows\.com", host))
    if (
        not allowed
        or parts.scheme != "https"
        or parts.port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or not parts.path
        or parts.path == "/"
    ):
        raise ValueError("Unsupported browser push endpoint")
    return endpoint


def validate_timezone(timezone):
    if not isinstance(timezone, str) or not 1 <= len(timezone) <= 100:
        raise ValueError("Invalid timezone")
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Invalid timezone") from exc


def validate_subscription(endpoint, p256dh, auth, timezone):
    validate_endpoint(endpoint)
    if decode_key(p256dh, 65)[0] != 4:
        raise ValueError("Invalid browser push public key")
    decode_key(auth, 16)
    validate_timezone(timezone)


def next_push_datetime(now, timezone, hour=9):
    zone = validate_timezone(timezone)
    local = now.astimezone(zone)
    due = datetime.combine(local.date(), time(hour), zone)
    if due <= local:
        due += timedelta(days=1)
    return due.astimezone(UTC)


def _tomorrow(now, timezone, hour):
    local = now.astimezone(validate_timezone(timezone))
    return datetime.combine(local.date() + timedelta(days=1), time(hour), local.tzinfo).astimezone(
        UTC
    )


def eligible_push_article_ids(session, user_id, article_ids):
    """Source removals or invalidated recommendation links cannot leak stale picks."""
    return set(
        session.scalars(
            select(Article.id)
            .join(UserRecommendation, UserRecommendation.article_id == Article.id)
            .where(
                UserRecommendation.user_id == user_id,
                Article.id.in_(article_ids),
                recommendation_eligibility(user_id),
            )
        )
    )


def schedule_daily_pushes(factory, batch=100, *, now=None, settings=None):
    """Freeze one unread, unsent snapshot pick; fan out once to current browsers."""
    settings = settings or get_web_push_settings()
    if not settings.web_push_enabled:
        return 0
    now = now or utcnow()
    with factory() as session:
        users = list(
            session.scalars(
                select(WebPushSubscription.user_id)
                .where(
                    WebPushSubscription.enabled.is_(True),
                    WebPushSubscription.authorization_expires_at > now,
                    WebPushSubscription.next_push_at <= now,
                )
                .group_by(WebPushSubscription.user_id)
                .order_by(func.min(WebPushSubscription.next_push_at), WebPushSubscription.user_id)
                .limit(batch)
            )
        )
    count = 0
    for user_id in users:
        with factory.begin() as session:
            account = session.scalar(
                select(UserAccount)
                .where(UserAccount.id == user_id)
                .with_for_update(skip_locked=True)
            )
            if account is None:
                continue
            subscriptions = session.scalars(
                select(WebPushSubscription)
                .where(
                    WebPushSubscription.user_id == user_id,
                    WebPushSubscription.enabled.is_(True),
                    WebPushSubscription.authorization_expires_at > now,
                )
                .order_by(WebPushSubscription.created_at, WebPushSubscription.id)
                .with_for_update()
            ).all()
            if not subscriptions or not any(sub.next_push_at <= now for sub in subscriptions):
                continue
            timezone = subscriptions[0].timezone
            local = now.astimezone(validate_timezone(timezone))
            if local.hour < settings.web_push_delivery_hour:
                for sub in subscriptions:
                    sub.next_push_at = next_push_datetime(
                        now, timezone, settings.web_push_delivery_hour
                    )
                continue
            latest = session.scalar(
                select(DailyMustReadPush)
                .where(DailyMustReadPush.user_id == user_id)
                .order_by(DailyMustReadPush.created_at.desc())
                .limit(1)
            )
            # Preserve frequency when a browser changes timezone or consent is toggled.
            if latest and (
                latest.selection_date >= local.date()
                or now - latest.created_at < timedelta(hours=20)
            ):
                for sub in subscriptions:
                    sub.next_push_at = _tomorrow(now, timezone, settings.web_push_delivery_hour)
                continue
            snapshot, preparing = ensure_snapshot(session, account, local.date(), timezone)
            if preparing:
                # Retry prepared recommendations fairly without monopolizing a batch.
                for sub in subscriptions:
                    sub.next_push_at = now + timedelta(minutes=5)
                continue
            articles, reasons, read_ids = read_snapshot(
                session,
                snapshot,
                FeedSettings.model_validate(account.feed_settings),
                include_details=False,
            )
            sent = set(
                session.scalars(
                    select(DailyMustReadPush.article_id)
                    .join(WebPushDelivery, WebPushDelivery.event_id == DailyMustReadPush.id)
                    .where(
                        DailyMustReadPush.user_id == user_id,
                        WebPushDelivery.accepted_at.is_not(None),
                    )
                )
            )
            eligible = eligible_push_article_ids(session, user_id, [item.id for item in articles])
            article = next(
                (
                    item
                    for item in articles
                    if item.id in eligible and item.id not in sent | set(read_ids)
                ),
                None,
            )
            for sub in subscriptions:
                sub.next_push_at = _tomorrow(now, timezone, settings.web_push_delivery_hour)
            if article is None:
                continue
            event = DailyMustReadPush(
                user_id=user_id,
                selection_date=local.date(),
                timezone=timezone,
                article_id=article.id,
                title=(article.ai_title or article.title)[:200],
                reason=reasons[str(article.id)][:300],
                created_at=now,
                expires_at=datetime.combine(
                    local.date() + timedelta(days=1), time(), local.tzinfo
                ).astimezone(UTC),
            )
            session.add(event)
            session.flush()
            session.add_all(
                WebPushDelivery(
                    event_id=event.id,
                    subscription_id=sub.id,
                    session_hash=sub.session_hash,
                    consent_id=sub.consent_id,
                    created_at=now,
                    available_at=now,
                )
                for sub in subscriptions
            )
            count += 1
    return count


def retry_push(job, error, now, expires_at, *, retry_after=0, retryable=True):
    """Stop attempts before local midnight; recovery follows the same bound."""
    job.error = error
    job.dispatched_at = None
    clear_lease(job)
    delay = max(min(3600, 30 * 2 ** max(0, job.attempts - 1)), retry_after)
    if (
        retryable
        and job.attempts < MAX_PUSH_ATTEMPTS
        and delay < (expires_at - now).total_seconds()
    ):
        job.status = "queued"
        job.available_at = now + timedelta(seconds=delay)
    else:
        job.status, job.finished_at = "failed", now


def recover_web_push(factory, batch, now):
    with factory.begin() as session:
        rows = session.execute(
            select(WebPushDelivery, DailyMustReadPush.expires_at)
            .join(DailyMustReadPush)
            .where(WebPushDelivery.status == "running", WebPushDelivery.lease_until < now)
            .order_by(WebPushDelivery.lease_until)
            .limit(batch)
            .with_for_update(of=WebPushDelivery, skip_locked=True)
        ).all()
        for job, expires_at in rows:
            retry_push(job, "Push worker interrupted; lease recovered", now, expires_at)
        return len(rows)

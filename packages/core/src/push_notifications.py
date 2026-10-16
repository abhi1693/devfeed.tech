"""Typed Web Push publication and bounded, resumable audience expansion."""

import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import WebPushDelivery, WebPushEvent, WebPushSubscription, utcnow
from devfeed_core.push_audience import PushAudience, web_push_recipient_ids
from devfeed_core.push_types import WebPushMessage as WebPushMessage
from devfeed_core.push_types import get_push_type
from devfeed_core.web_push import get_web_push_settings


def enqueue_web_push(
    session: Session,
    *,
    event_key: str,
    message: WebPushMessage,
    audience: PushAudience,
    expires_at: datetime,
    now: datetime | None = None,
) -> uuid.UUID | None:
    """Publish in the business transaction; callers never perform HTTP/fan-out.

    Reusing an event key returns the original event only when its immutable
    message and audience agree. A failed surrounding transaction publishes nothing.
    """
    get_push_type(message.kind).validate(message, audience)
    if not event_key or len(event_key) > 255 or not event_key.strip():
        raise ValueError("Web Push event keys must contain 1-255 characters")
    now = now or utcnow()
    if not expires_at.tzinfo or not now.tzinfo or expires_at <= now:
        raise ValueError("Web Push expiry must be a future timezone-aware time")
    payload = message.model_dump(mode="json")
    # Leave room for the encrypted receiver envelope and its routing identifiers.
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 2800:
        raise ValueError("Browser notification message exceeds the payload limit")
    if not get_web_push_settings().web_push_enabled:
        return None
    target = audience.model_dump(mode="json")
    identifier = session.scalar(
        insert(WebPushEvent)
        .values(
            event_key=event_key,
            kind=message.kind,
            payload=payload,
            audience=target,
            created_at=now,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=[WebPushEvent.event_key])
        .returning(WebPushEvent.id)
    )
    if identifier is not None:
        return identifier
    existing = session.scalar(select(WebPushEvent).where(WebPushEvent.event_key == event_key))
    if (
        existing is None
        or existing.kind != message.kind
        or existing.payload != payload
        or existing.audience != target
        or existing.expires_at != expires_at
    ):
        raise ValueError("Web Push event key was already used for a different notification")
    return existing.id


def expand_web_push_events(factory, batch=100, *, now=None):
    """Commit one UUID page with its browser outbox before advancing the cursor.

    The tick budget bounds both account rows and event pages. This expands many
    one-user Must Reads together. Unstarted events take priority over the next
    page of a partially expanded audience.
    """
    if not 1 <= batch <= 1000:
        raise ValueError("Web Push expansion batches must contain 1-1000 accounts")
    if not get_web_push_settings().web_push_enabled:
        return 0
    now = now or utcnow()
    processed = 0
    for _ in range(batch):
        if processed >= batch:
            break
        with factory.begin() as session:
            event = session.scalar(
                select(WebPushEvent)
                .where(WebPushEvent.expanded_at.is_(None))
                .order_by(
                    WebPushEvent.recipient_cursor.is_not(None),
                    WebPushEvent.created_at,
                    WebPushEvent.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                break
            if event.expires_at <= now:
                event.expanded_at = now
                continue
            try:
                audience = PushAudience.model_validate(event.audience)
                message = WebPushMessage.model_validate(event.payload)
                if message.kind != event.kind:
                    raise ValueError("Notification type changed")
                get_push_type(event.kind).validate(message, audience)
            except ValueError:
                # Unregistered/invalid types never recruit users or reach a browser.
                event.expanded_at = now
                continue
            page_size = batch - processed
            recipients = list(
                session.scalars(
                    web_push_recipient_ids(
                        audience,
                        event.kind,
                        event.created_at,
                        now,
                        after=event.recipient_cursor,
                        batch=page_size,
                    )
                )
            )
            if recipients:
                subscriptions = session.scalars(
                    select(WebPushSubscription).where(
                        WebPushSubscription.user_id.in_(recipients),
                        WebPushSubscription.enabled.is_(True),
                        WebPushSubscription.authorization_expires_at > now,
                        WebPushSubscription.created_at <= event.created_at,
                        WebPushSubscription.allowed_kinds.contains([event.kind]),
                    )
                ).all()
                if subscriptions:
                    session.execute(
                        insert(WebPushDelivery)
                        .values(
                            [
                                {
                                    "event_id": event.id,
                                    "user_id": sub.user_id,
                                    "subscription_id": sub.id,
                                    "session_hash": sub.session_hash,
                                    "consent_id": sub.consent_id,
                                    "created_at": now,
                                    "available_at": now,
                                }
                                for sub in subscriptions
                            ]
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                WebPushDelivery.event_id,
                                WebPushDelivery.subscription_id,
                            ]
                        )
                    )
                event.recipient_cursor = recipients[-1]
                processed += len(recipients)
            if len(recipients) < page_size:
                event.expanded_at = now
    return processed

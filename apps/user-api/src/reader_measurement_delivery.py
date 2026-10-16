"""One bounded, optional GA4 delivery attempt for verified reader milestones.

The first-party ledger is authoritative. A GA4 2xx response acknowledges HTTP
transport only; it does not prove ingestion. An interrupted attempt is never
retried automatically because GA4 does not promise deduplication for these events.
"""

import logging
import re
import uuid
from datetime import timedelta

import httpx
from devfeed_core.db import session_factory
from devfeed_core.models import ReaderMilestoneDelivery, utcnow
from devfeed_core.reader_acquisition import AcquisitionContext
from sqlalchemy import select

from devfeed_user_api.config import Settings

logger = logging.getLogger(__name__)
COLLECTION_URL = "https://www.google-analytics.com/mp/collect"
EVENTS = frozenset({"sign_up", "reader_activation"})
PLATFORMS = frozenset({"web", "chrome_extension", "edge_extension"})


def build_ga4_payload(delivery: ReaderMilestoneDelivery, *, campaigns=()) -> dict:
    """Allow only the narrow diagnostic contract, including no account identity."""
    if delivery.event_name not in EVENTS:
        raise ValueError("Unsupported reader milestone")
    context = delivery.payload
    if not isinstance(context, dict):
        raise ValueError("Invalid reader milestone payload")
    client = context.get("client_id")
    session = context.get("session_id")
    platform = context.get("client_platform")
    if (
        not isinstance(client, str)
        or not re.fullmatch(r"[0-9]{1,10}\.[0-9]{1,10}", client)
        or type(session) is not int
        or not 0 < session <= 2**53 - 1
        or platform not in PLATFORMS
        or delivery.occurred_at.tzinfo is None
    ):
        raise ValueError("Reader milestone has no measurable browser identity")
    params = {
        "session_id": session,
        "client_platform": platform,
        "measurement_version": "reader_v1",
    }
    AcquisitionContext.model_validate(
        {
            "version": 1,
            "status": context.get("acquisition_status"),
            "captured_at": int(delivery.occurred_at.timestamp()),
            "measurement": "eligible",
            **{
                field: context.get(field)
                for field in (
                    "source",
                    "medium",
                    "campaign",
                    "landing_route",
                    "client_platform",
                    "client_id",
                    "session_id",
                )
            },
        }
    )
    if context.get("campaign") is not None and context["campaign"] not in campaigns:
        raise ValueError("Reader milestone has no permitted acquisition context")
    # These are deliberately custom acquisition dimensions. Do not overwrite GA's
    # session source or pretend that another device shares the signup GA identity.
    for field, output in (
        ("acquisition_status", "acquisition_state"),
        ("source", "acquisition_source"),
        ("medium", "acquisition_medium"),
        ("campaign", "acquisition_campaign"),
        ("landing_route", "landing_route"),
    ):
        value = context.get(field)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
                raise ValueError("Invalid acquisition dimension")
            params[output] = value
    return {
        "client_id": client,
        "timestamp_micros": int(delivery.occurred_at.timestamp() * 1_000_000),
        "consent": {"ad_user_data": "DENIED", "ad_personalization": "DENIED"},
        "events": [{"name": delivery.event_name, "params": params}],
    }


def _enabled(settings: Settings, platform: str | None) -> bool:
    return (
        isinstance(platform, str)
        and settings.reader_measurement_enabled
        and (
            settings.analytics_enabled
            if platform == "web"
            else settings.extension_analytics_enabled
            if platform in PLATFORMS
            else False
        )
    )


def _claim(settings, user_id, event_name):
    with session_factory().begin() as session:
        row = session.scalar(
            select(ReaderMilestoneDelivery)
            .where(
                ReaderMilestoneDelivery.user_id == user_id,
                ReaderMilestoneDelivery.event_name == event_name,
            )
            .with_for_update(skip_locked=True)
        )
        if row is None or row.status != "pending" or row.attempts:
            return None
        now = utcnow()
        if (
            not isinstance(row.payload, dict)
            or not isinstance(row.payload.get("client_platform"), str)
            or row.payload["client_platform"] not in PLATFORMS
        ):
            row.status, row.reason, row.finished_at = "unavailable", "invalid_payload", now
            return None
        if not _enabled(settings, row.payload.get("client_platform")):
            row.status, row.reason, row.finished_at = "unavailable", "analytics_disabled", now
            return None
        if not settings.ga4_measurement_id or not settings.reader_ga_api_secret:
            row.status, row.reason, row.finished_at = "unavailable", "not_configured", now
            return None
        if row.occurred_at < now - timedelta(hours=24) or row.occurred_at > now:
            # A queued event must not be rewritten to a new time/session by GA4.
            row.status, row.reason, row.finished_at = "unavailable", "stale_measurement", now
            return None
        try:
            payload = build_ga4_payload(row, campaigns=settings.reader_acquisition_campaigns)
        except (ValueError, TypeError, AttributeError):
            row.status, row.reason, row.finished_at = "unavailable", "invalid_payload", now
            return None
        row.status, row.reason = "attempted", "ingestion_unverified"
        row.attempts, row.attempted_at = 1, now
        return payload


def _finish(user_id, event_name, status, reason):
    with session_factory().begin() as session:
        row = session.scalar(
            select(ReaderMilestoneDelivery)
            .where(
                ReaderMilestoneDelivery.user_id == user_id,
                ReaderMilestoneDelivery.event_name == event_name,
            )
            .with_for_update()
        )
        if row is not None and row.status == "attempted":
            row.status, row.reason, row.finished_at = status, reason, utcnow()


def send_reader_milestone(settings: Settings, *, user_id: str | uuid.UUID, event_name: str) -> str:
    """Never make authentication or engagement depend on external measurement."""
    try:
        identifier = uuid.UUID(str(user_id))
        if event_name not in EVENTS:
            return "unavailable"
        secret = settings.reader_ga_api_secret
        payload = _claim(settings, identifier, event_name)
        if payload is None:
            return "not_sent"
        if secret is None:
            return "unknown"
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                response = client.post(
                    COLLECTION_URL,
                    params={
                        "measurement_id": settings.ga4_measurement_id,
                        "api_secret": secret.get_secret_value(),
                    },
                    json=payload,
                )
            status = "http_accepted" if 200 <= response.status_code < 300 else "failed"
            reason = "ingestion_unverified" if status == "http_accepted" else "http_rejected"
        except httpx.HTTPError:
            status, reason = "unknown", "transport_unresolved"
        _finish(identifier, event_name, status, reason)
        return status
    except Exception:
        # Never include request URLs, provider bodies, identities, or exceptions:
        # the provider URL contains the server-only API secret.
        logger.warning("reader_measurement_unavailable")
        return "unknown"

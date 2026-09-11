"""RQ delivery to Chimely, outside ingestion transactions and worker queues."""

import logging
import uuid
from datetime import timedelta

import httpx
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import LEASE_SECONDS, owned_job
from devfeed_core.models import NotificationDelivery, utcnow
from sqlalchemy import select

from devfeed_notifications.config import get_settings

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 20
# Chimely defaults to 30-day idempotency retention. Never silently re-send a
# delivery that may already exist once that server-side protection has expired.
MAX_DELIVERY_AGE = timedelta(days=28)


def retry_delivery(job, error: str, retry_after: int = 0):
    job.error = error
    job.dispatched_at = job.lease_until = job.lease_token = None
    if job.attempts >= MAX_ATTEMPTS or utcnow() - job.created_at >= MAX_DELIVERY_AGE:
        job.status, job.finished_at = "failed", utcnow()
    else:
        job.status = "queued"
        delay = min(3600, max(30 * 2 ** min(job.attempts - 1, 7), retry_after))
        job.available_at = utcnow() + timedelta(seconds=delay)


def recover_notifications(factory, batch, now):
    with factory.begin() as session:
        jobs = session.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "running", NotificationDelivery.lease_until < now)
            .order_by(NotificationDelivery.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            retry_delivery(job, "Delivery worker interrupted; lease recovered")
        return len(jobs)


def deliver_notification(job_id: str):
    with job_log_context("notifications", job_id):
        try:
            _deliver(uuid.UUID(job_id))
        except Exception:
            logger.exception("notification_runtime_failed")
            raise


def _deliver(identifier: uuid.UUID):
    settings = get_settings()
    if not settings.notifications_enabled:
        return  # Scheduler can redispatch this row after re-enabling.
    factory = session_factory()
    with factory.begin() as session:
        job = session.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == identifier)
            .with_for_update()
        )
        if job is None or job.status != "queued" or job.available_at > utcnow():
            return
        if utcnow() - job.created_at >= MAX_DELIVERY_AGE:
            job.status, job.finished_at = "failed", utcnow()
            job.error = "Delivery exceeded the safe idempotency window; manual review required"
            return
        if job.category == "feed.topic.new":
            from devfeed_core.feed_notifications import delivery_is_current

            if not delivery_is_current(session, job):
                job.status, job.finished_at = "succeeded", utcnow()
                job.error = "Skipped: article or followed topic is no longer eligible"
                return
        token = start_job(job, utcnow(), LEASE_SECONDS)
        payload = {
            "idempotency_key": f"devfeed:{job.id}",
            "category": job.category,
            "payload": job.payload,
        }
        if job.category == "feed.topic.new":
            payload["payload"] = {
                k: v for k, v in job.payload.items() if k not in {"article_id", "user_id"}
            }
        attempt = job.attempts
        # Environments own their API keys. No fallback to another audience if a
        # destination is disabled or misconfigured; operational messages are private.
        secret = getattr(settings, f"chimely_{job.audience}_api_key", None)
        environment = getattr(settings, f"chimely_{job.audience}_environment", None)
        endpoint = "/v1/notifications" if job.subscriber_id is not None else "/v1/broadcasts"
        if job.subscriber_id is not None:
            payload["subscriber_id"] = job.subscriber_id
    logger.info("notification_delivery_started", extra={"attempt": attempt})
    error, retry_after = None, 0
    assert settings.chimely_api_url
    try:
        # A trusted, configured internal service, not an arbitrary source URL.
        # No redirects, ambient proxy credentials, app cookies or raw errors.
        if not environment or not secret:
            raise httpx.ConnectError("Destination is not configured")
        with (
            httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client,
            client.stream(
                "POST",
                settings.chimely_api_url.rstrip("/") + endpoint,
                headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
                json=payload,
            ) as response,
        ):
            if response.status_code not in {200, 201}:
                error = f"Notification service returned HTTP {response.status_code}"
                hint = response.headers.get("retry-after", "")
                retry_after = min(int(hint), 3600) if hint.isdigit() and len(hint) < 8 else 0
    except httpx.HTTPError:
        error = "Notification service is temporarily unreachable"
    with factory.begin() as session:
        job = owned_job(session, NotificationDelivery, identifier, token)
        if job is None:
            logger.warning("notification_lease_lost")
            return
        if error:
            retry_delivery(job, error, retry_after)
        else:
            job.status, job.finished_at, job.error = "succeeded", utcnow(), None
            job.lease_token = job.lease_until = None
        status = job.status
    logger.log(
        logging.WARNING if error else logging.INFO,
        "notification_delivery_failed" if error else "notification_delivery_succeeded",
        extra={"job_status": status, "attempt": attempt},
    )

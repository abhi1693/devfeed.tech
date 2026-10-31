"""RQ delivery to Chimely, outside ingestion transactions and worker queues."""

import json
import logging
import time
import uuid
from contextlib import nullcontext
from datetime import datetime

import httpx
from devfeed_core.db import session_factory
from devfeed_core.job_lifecycle import start_job
from devfeed_core.job_logs import job_log_context
from devfeed_core.jobs import LEASE_SECONDS, owned_job
from devfeed_core.models import NotificationDelivery, utcnow
from devfeed_core.notification_delivery import DELIVERY_POLICY
from devfeed_core.notification_delivery import recover_notifications as recover_notifications
from devfeed_core.notification_delivery import retry_delivery as retry_delivery
from rq.timeouts import JobTimeoutException
from sqlalchemy import select, text, update

from devfeed_notifications.config import get_settings

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = DELIVERY_POLICY.max_attempts
MAX_DELIVERY_AGE = DELIVERY_POLICY.max_age


def deliver_notification(job_id: str) -> None:
    if job_id.startswith("{"):
        _deliver_batch(job_id)
        return
    with job_log_context("notifications", job_id):
        try:
            _deliver(uuid.UUID(job_id))
        except Exception:
            logger.exception("notification_runtime_failed")
            raise


def _parse_batch(payload: str) -> tuple[list[uuid.UUID], datetime]:
    if len(payload) > 4096:
        raise ValueError("Notification batch is too large")
    body = json.loads(payload)
    if (
        not isinstance(body, dict)
        or set(body) != {"deliveries", "published_at"}
        or not isinstance(body["deliveries"], list)
    ):
        raise ValueError("Invalid notification batch")
    identifiers = [uuid.UUID(value) for value in body["deliveries"]]
    if not 1 <= len(identifiers) <= 10 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Notification batch requires 1-10 distinct deliveries")
    published_at = datetime.fromisoformat(body["published_at"])
    if published_at.tzinfo is None:
        raise ValueError("Notification batch requires an aware publication time")
    return identifiers, published_at


def _release_batch(identifiers: list[uuid.UUID], published_at: datetime) -> None:
    try:
        with session_factory().begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '1s'"))
            # New dispatches own their own timestamp; claims/retries and
            # completed rows are never reset by an older transport batch.
            session.execute(
                update(NotificationDelivery)
                .where(
                    NotificationDelivery.id.in_(identifiers),
                    NotificationDelivery.status == "queued",
                    NotificationDelivery.dispatched_at == published_at,
                )
                .values(dispatched_at=None)
            )
    except JobTimeoutException:
        raise  # Cleanup remains inside RQ's hard deadline.
    except Exception:
        # PostgreSQL remains the outbox even if best-effort release fails.
        logger.warning("notification_batch_release_failed")


def _deliver_batch(payload: str) -> None:
    identifiers, published_at = _parse_batch(payload)
    deadline = time.monotonic() + 20
    failures, completed = 0, 0
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            for identifier in identifiers:
                if time.monotonic() >= deadline:
                    break
                with job_log_context("notifications", str(identifier)):
                    try:
                        _deliver(
                            identifier,
                            client=client,
                            timeout=max(0.1, min(10, deadline - time.monotonic())),
                        )
                    except JobTimeoutException:
                        raise  # RQ's 30-second batch deadline must not be swallowed.
                    except Exception:
                        failures += 1
                        logger.exception("notification_runtime_failed")
                completed += 1
    finally:
        if completed < len(identifiers) or failures:
            _release_batch(identifiers, published_at)
    if failures:
        raise RuntimeError("One or more notification deliveries failed")


def _deliver(
    identifier: uuid.UUID, *, client: httpx.Client | None = None, timeout: float = 10
) -> None:
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
        if DELIVERY_POLICY.expired(job, utcnow()):
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
        if client is not None:
            # A pooled connection may serve different audiences; no response
            # cookie may become another recipient's ambient credential.
            client.cookies.clear()
        with (
            nullcontext(client)
            if client is not None
            else httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client,
            client.stream(
                "POST",
                settings.chimely_api_url.rstrip("/") + endpoint,
                headers={"Authorization": f"Bearer {secret.get_secret_value()}"},
                json=payload,
                timeout=timeout,
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

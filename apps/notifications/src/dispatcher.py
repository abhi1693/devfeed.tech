"""Notification dispatch adapter called by the common scheduler."""

import logging
from datetime import timedelta

from devfeed_core.jobs import JOB_TIMEOUT_SECONDS, REDISPATCH_SECONDS
from devfeed_core.models import NotificationDelivery
from sqlalchemy import or_, select

logger = logging.getLogger(__name__)


def dispatch(factory, queue, batch, now):
    dispatched = 0
    for _ in range(batch):
        with factory.begin() as session:
            job = session.scalar(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.status == "queued",
                    NotificationDelivery.available_at <= now,
                    or_(
                        NotificationDelivery.dispatched_at.is_(None),
                        NotificationDelivery.dispatched_at
                        < now - timedelta(seconds=REDISPATCH_SECONDS),
                    ),
                )
                .order_by(NotificationDelivery.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                break
            # Publish before commit. A targeted claim waits for this row lock;
            # any duplicate after a crash is harmless and keeps its Chimely key.
            queue.enqueue(
                "devfeed_notifications.delivery.deliver_notification",
                str(job.id),
                job_timeout=JOB_TIMEOUT_SECONDS,
                result_ttl=0,
                failure_ttl=86400,
                ttl=REDISPATCH_SECONDS,
            )
            job.dispatched_at = now
            identifier = str(job.id)
        dispatched += 1
        logger.info("notification_dispatched", extra={"job_id": identifier})
    return dispatched

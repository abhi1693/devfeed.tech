"""Durable delivery policy; scheduler and admin never load the HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_lifecycle import clear_lease
from devfeed_core.models import NotificationDelivery, utcnow


@dataclass(frozen=True)
class DeliveryPolicy:
    max_attempts: int = 20
    # Leave a margin below Chimely's 30-day idempotency retention.
    max_age: timedelta = timedelta(days=28)

    def expired(self, job: NotificationDelivery, now: datetime) -> bool:
        return now - job.created_at >= self.max_age

    def retry(
        self, job: NotificationDelivery, error: str, now: datetime, retry_after: int = 0
    ) -> None:
        job.error = error
        job.dispatched_at = None
        clear_lease(job)
        if job.attempts >= self.max_attempts or self.expired(job, now):
            job.status, job.finished_at = "failed", now
        else:
            job.status = "queued"
            delay = min(3600, max(30 * 2 ** min(job.attempts - 1, 7), retry_after))
            job.available_at = now + timedelta(seconds=delay)


DELIVERY_POLICY = DeliveryPolicy()


def retry_delivery(job: NotificationDelivery, error: str, retry_after: int = 0) -> None:
    DELIVERY_POLICY.retry(job, error, utcnow(), retry_after)


def recover_notifications(factory: sessionmaker[Session], batch: int, now: datetime) -> int:
    with factory.begin() as session:
        jobs = session.scalars(
            select(NotificationDelivery)
            .options(*JOB_DEFINITIONS["notifications"].metadata_options())
            .where(NotificationDelivery.status == "running", NotificationDelivery.lease_until < now)
            .order_by(NotificationDelivery.lease_until)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            DELIVERY_POLICY.retry(job, "Delivery worker interrupted; lease recovered", now)
        return len(jobs)

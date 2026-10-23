"""Delivery recovery, delivery attempts and manual retries share one safety window."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import NotificationDelivery, utcnow
from devfeed_core.notification_delivery import DELIVERY_POLICY, recover_notifications
from sqlalchemy import select


@pytest.mark.parametrize(
    "age", [timedelta(days=28) - timedelta(microseconds=1), timedelta(days=28)]
)
def test_delivery_expiry_has_an_exact_inclusive_boundary(age):
    now = utcnow()
    job = NotificationDelivery(created_at=now - age)
    assert DELIVERY_POLICY.expired(job, now) == (age >= timedelta(days=28))


@pytest.mark.parametrize(
    "attempts,hint,delay", [(1, 0, 30), (2, 120, 120), (19, 0, 3600), (2, 9000, 3600)]
)
def test_retry_keeps_identity_and_clears_the_owned_lease(attempts, hint, delay):
    now, identifier = utcnow(), uuid.uuid4()
    job = NotificationDelivery(
        id=identifier,
        status="running",
        attempts=attempts,
        created_at=now,
        lease_token=uuid.uuid4(),
        lease_until=now + timedelta(minutes=5),
        dispatched_at=now,
        dedup_key="same-delivery",
    )
    DELIVERY_POLICY.retry(job, "unavailable", now, hint)
    assert job.id == identifier and job.dedup_key == "same-delivery"
    assert job.status == "queued" and job.error == "unavailable"
    assert job.available_at == now + timedelta(seconds=delay)
    assert job.lease_token is job.lease_until is job.dispatched_at is None


@pytest.mark.parametrize("age,attempts", [(28, 1), (0, 20)])
def test_retry_exhaustion_requires_manual_review(age, attempts):
    now = utcnow()
    job = NotificationDelivery(created_at=now - timedelta(days=age), attempts=attempts)
    DELIVERY_POLICY.retry(job, "unavailable", now)
    assert job.status == "failed" and job.finished_at == now


@pytest.mark.integration
def test_recovery_is_bounded_skips_locked_rows_and_preserves_payload(database):
    now = utcnow()
    identifiers = []
    with database.begin() as session:
        for index, (age, attempts, lease) in enumerate(
            [(0, 1, -4), (28, 1, -3), (0, 20, -2), (0, 1, 1)]
        ):
            job = NotificationDelivery(
                event_key=f"event-{index}",
                dedup_key=f"delivery-{index}",
                audience="admin",
                category="product.release",
                payload={"private": "never needed for recovery"},
                status="running",
                attempts=attempts,
                created_at=now - timedelta(days=age),
                lease_until=now + timedelta(minutes=lease),
                lease_token=uuid.uuid4(),
                dispatched_at=now,
            )
            session.add(job)
            session.flush()
            identifiers.append(job.id)
    # A delivery owned by another transaction must not delay other recovery.
    with database.begin() as locked:
        locked.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == identifiers[0])
            .with_for_update()
        )
        assert recover_notifications(database, 1, now) == 1
        with database() as session:
            assert session.get(NotificationDelivery, identifiers[1]).status == "failed"
            assert session.get(NotificationDelivery, identifiers[2]).status == "running"
    assert recover_notifications(database, 10, now) == 2
    assert recover_notifications(database, 10, now) == 0
    with database() as session:
        jobs = [session.get(NotificationDelivery, identifier) for identifier in identifiers]
        assert [job.status for job in jobs] == ["queued", "failed", "failed", "running"]
        assert all(job.payload == {"private": "never needed for recovery"} for job in jobs)
        assert jobs[0].available_at == now + timedelta(seconds=30)

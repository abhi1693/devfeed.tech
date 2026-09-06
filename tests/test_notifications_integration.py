"""Application outbox transactions; requires explicitly supplied disposable services."""

import uuid

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, NotificationDelivery, Source
from devfeed_core.notifications import NotificationMessage, enqueue_notification
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


@pytest.fixture
def enabled(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    return database


def test_job_result_and_notification_commit_or_rollback_together(enabled):
    with enabled.begin() as session:
        source = Source(
            name="Publisher",
            feed_url="https://example.com/feed",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        job = IngestionJob(source_id=source.id, status="running", attempts=1)
        session.add(job)
        session.flush()
        identifier = job.id
    with enabled() as session:
        job = session.get(IngestionJob, identifier)
        job.status, job.articles_created = "succeeded", 2
        session.flush()
        assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == 1
        session.rollback()
    with enabled.begin() as session:
        job = session.get(IngestionJob, identifier)
        assert job.status == "running"
        assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == 0
        job.status, job.articles_created = "succeeded", 2
    with enabled() as session:
        event = session.scalar(select(NotificationDelivery))
        assert event.audience == "admin" and event.subscriber_id is None
        assert event.payload["action_url"] == f"/ingestion-jobs/{identifier}"
        assert session.get(IngestionJob, identifier).status == "succeeded"


def test_announcement_idempotency_per_audience_and_recipient(enabled):
    event = NotificationMessage(
        category="product.release", title="Released", body="New features", action_url="/updates"
    )
    identifier = f"user_{uuid.uuid4().hex}"
    with enabled.begin() as session:
        first = enqueue_notification(
            session, "release:one", event, audience="user", subscriber_id=identifier
        )
        assert first is not None
        assert (
            enqueue_notification(
                session, "release:one", event, audience="user", subscriber_id=identifier
            )
            is None
        )
        assert enqueue_notification(session, "release:one", event, audience="user") is not None
        assert enqueue_notification(session, "release:one", event, audience="admin") is not None
    with enabled() as session:
        assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == 3

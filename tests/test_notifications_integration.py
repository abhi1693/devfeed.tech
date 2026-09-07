"""Application outbox transactions; requires explicitly supplied disposable services."""

import uuid

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    IngestionJob,
    NotificationDelivery,
    Source,
    TopicAnalysisJob,
    TopicProposal,
)
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
        assert event.payload["action_url"] == f"/jobs/ingestion/{identifier}"
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


@pytest.mark.parametrize(
    "status,outcome,attempts,severity",
    [
        ("failed", None, 3, "error"),
        ("queued", None, 1, "warning"),
        ("succeeded", "enriched", 1, "success"),
        ("succeeded", "no_additions", 1, None),
    ],
)
def test_topic_research_transitions_create_atomic_deliveries(
    enabled, status, outcome, attempts, severity
):
    with enabled.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="example",
            action="create",
            origin="import",
            source_name="Test",
            proposed={"name": "Example", "slug": "example", "kind": "technology"},
            evidence=[],
            created_by={"subject": "test"},
        )
        session.add(proposal)
        session.flush()
        job = TopicAnalysisJob(
            proposal_id=proposal.id,
            status="running",
            attempts=attempts,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={"subject": "test"},
            prompt_version="test",
        )
        session.add(job)
        session.flush()
        identifier = job.id
        assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == 0
    for commit in [False, True]:
        with enabled() as session:
            job = session.get(TopicAnalysisJob, identifier)
            assert job.status == "running"
            assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == 0
            job.status, job.outcome = status, outcome
            job.error = "private upstream details" if status in {"failed", "queued"} else None
            session.flush()
            event = session.scalar(select(NotificationDelivery))
            if severity is None:
                assert event is None
            else:
                assert event.category == "jobs.topic-analysis" and event.audience == "admin"
                assert event.payload["severity"] == severity
                assert event.payload["action_url"] == f"/jobs/analysis/topics/{identifier}"
                assert "private" not in str(event.payload)
            if commit:
                session.commit()
            else:
                session.rollback()
    with enabled.begin() as session:
        job = session.get(TopicAnalysisJob, identifier)
        job.error = None  # Non-transition changes cannot emit duplicate notifications.
        session.flush()
        assert session.scalar(select(func.count()).select_from(NotificationDelivery)) == int(
            severity is not None
        )

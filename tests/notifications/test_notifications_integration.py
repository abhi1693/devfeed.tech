"""Application outbox transactions; requires explicitly supplied disposable services."""

import uuid

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import (
    IngestionJob,
    NotificationDelivery,
    Source,
    Topic,
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
@pytest.mark.parametrize("relationships", [False, True])
def test_topic_research_transitions_create_atomic_deliveries(
    enabled, status, outcome, attempts, severity, relationships
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
        topic = Topic(name="React", slug="react", kind="technology", status="active")
        session.add(topic)
        session.flush()
        job = TopicAnalysisJob(
            proposal_id=None if relationships else proposal.id,
            topic_id=topic.id if relationships else None,
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
                category = "relationship-research" if relationships else "topic-analysis"
                assert event.category == f"jobs.{category}.{severity}"
                assert event.audience == "admin"
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


@pytest.mark.parametrize("size", [1, 10])
def test_transport_batch_amortizes_startup_and_keeps_individual_retry_identity(
    database, monkeypatch, size
):
    import hashlib
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace

    import httpx
    from devfeed_core.config import get_settings as core_settings
    from devfeed_core.job_dispatch import dispatch_jobs
    from devfeed_core.models import utcnow
    from devfeed_notifications import delivery
    from devfeed_notifications.config import Settings

    settings = Settings(
        _env_file=None,
        notifications_enabled=True,
        chimely_api_url="http://chimely.invalid:8080",
        chimely_admin_environment="admin",
        chimely_admin_api_key="admin-key",
        chimely_user_environment="user",
        chimely_user_api_key="user-key",
    )
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery, "session_factory", lambda: database)
    monkeypatch.setattr(core_settings(), "notification_delivery_batch_size", size)
    identifiers = []
    with database.begin() as session:
        for i in range(100):
            key = f"batch-fixture:{i}"
            row = NotificationDelivery(
                event_key=key,
                dedup_key=hashlib.sha256(key.encode()).hexdigest(),
                audience="admin" if i % 2 == 0 else "user",
                category="release.changed",
                payload={"title": "Release", "body": "Ready"},
                available_at=utcnow(),
                created_at=utcnow(),
                status="queued",
                attempts=0,
            )
            session.add(row)
            session.flush()
            identifiers.append(row.id)
    carriers = []
    queue = SimpleNamespace(
        enqueue=lambda handler, payload, **options: carriers.append((handler, payload, options))
    )
    assert dispatch_jobs(database, queue, 100, utcnow(), kind="notifications") == 100
    assert len(carriers) == 100 // size
    consumer_now = utcnow()
    monkeypatch.setattr(delivery, "utcnow", lambda: consumer_now)
    clients, sent = [], []

    def request(request):
        body = json.loads(request.content)
        identifier = uuid.UUID(body["idempotency_key"].removeprefix("devfeed:"))
        position = identifiers.index(identifier)
        assert request.headers["authorization"] == (
            "Bearer admin-key" if position % 2 == 0 else "Bearer user-key"
        )
        assert "cookie" not in request.headers
        sent.append(identifier)
        return httpx.Response(
            429 if position == 0 else 201,
            headers={"retry-after": "5", "set-cookie": "private=session; Path=/"},
        )

    original_client = httpx.Client

    @contextmanager
    def client(**options):
        clients.append(options)
        with original_client(**options, transport=httpx.MockTransport(request)) as value:
            yield value

    monkeypatch.setattr(delivery.httpx, "Client", client)
    for _, payload, _ in carriers:
        delivery.deliver_notification(payload)
    assert len(clients) == len(carriers)
    assert len(sent) == 100 and len(set(sent)) == 100
    with database() as session:
        rows = {row.id: row for row in session.scalars(select(NotificationDelivery))}
        assert rows[identifiers[0]].status == "queued"
        assert rows[identifiers[0]].available_at > consumer_now
        assert all(row.attempts == 1 for row in rows.values())
        assert all(row.status == "succeeded" for key, row in rows.items() if key != identifiers[0])
    for _, payload, _ in carriers:
        delivery.deliver_notification(payload)
    assert len(sent) == 100  # Successful and delayed rows are not re-sent by transport replay.


def test_batch_deadline_releases_only_its_untouched_dispatches(database, monkeypatch):
    import hashlib
    import json
    from datetime import timedelta
    from types import SimpleNamespace

    from devfeed_core.models import utcnow
    from devfeed_notifications import delivery

    stamp = utcnow()
    identifiers = []
    with database.begin() as session:
        for i in range(3):
            row = NotificationDelivery(
                event_key=f"deadline:{i}",
                dedup_key=hashlib.sha256(str(i).encode()).hexdigest(),
                audience="admin",
                category="release.changed",
                payload={},
                available_at=stamp,
                created_at=stamp,
                dispatched_at=stamp,
                status="queued",
                attempts=0,
            )
            session.add(row)
            session.flush()
            identifiers.append(row.id)
    with database.begin() as session:
        session.get(NotificationDelivery, identifiers[2]).dispatched_at = stamp + timedelta(
            seconds=1
        )
    clock = SimpleNamespace(value=0)
    monkeypatch.setattr(delivery, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(delivery, "session_factory", lambda: database)

    def finish(identifier, **kwargs):
        with database.begin() as session:
            session.get(NotificationDelivery, identifier).status = "succeeded"
        clock.value = 25

    monkeypatch.setattr(delivery, "_deliver", finish)
    delivery.deliver_notification(
        json.dumps({"deliveries": list(map(str, identifiers)), "published_at": stamp.isoformat()})
    )
    with database() as session:
        assert session.get(NotificationDelivery, identifiers[0]).status == "succeeded"
        assert session.get(NotificationDelivery, identifiers[1]).dispatched_at is None
        assert session.get(NotificationDelivery, identifiers[2]).dispatched_at == stamp + timedelta(
            seconds=1
        )


def test_grouped_transport_repairs_lost_redis_membership_with_the_same_batch_id(
    database, monkeypatch
):
    import hashlib
    from datetime import timedelta

    from devfeed_aggregator.queue import get_queue
    from devfeed_core.config import get_settings
    from devfeed_core.job_dispatch import dispatch_jobs
    from devfeed_core.jobs import REDISPATCH_SECONDS
    from devfeed_core.models import utcnow

    monkeypatch.setattr(get_settings(), "notification_delivery_batch_size", 3)
    now = utcnow()
    with database.begin() as session:
        for i in range(3):
            session.add(
                NotificationDelivery(
                    event_key=f"restore:{i}",
                    dedup_key=hashlib.sha256(str(i).encode()).hexdigest(),
                    audience="admin",
                    category="release.changed",
                    payload={},
                    available_at=now,
                    created_at=now,
                    status="queued",
                    attempts=0,
                )
            )
    queue = get_queue("notifications")
    try:
        assert dispatch_jobs(database, queue, 10, now, kind="notifications") == 3
        [first] = queue.get_job_ids()
        assert queue.connection.lpop(queue.key) is not None
        assert queue.count == 0
        later = now + timedelta(seconds=REDISPATCH_SECONDS + 1)
        assert dispatch_jobs(database, queue, 10, later, kind="notifications") == 3
        assert queue.get_job_ids() == [first]
        # Duplicate dispatch inside its freshness window publishes nothing.
        assert dispatch_jobs(database, queue, 10, later, kind="notifications") == 0
        assert queue.get_job_ids() == [first]
    finally:
        queue.connection.close()

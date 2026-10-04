"""Retry selection and dispatch policy without network or production services."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import job_retries, models
from devfeed_core.services import OperationConflict, RecordNotFound
from sqlalchemy import Column, MetaData, Table, create_engine, select

NOW = datetime(2026, 10, 4, tzinfo=UTC)
ID = uuid.UUID(int=10)
ACTOR = {"subject": "reviewer"}


@pytest.mark.parametrize(
    "model,subject",
    [
        (models.IngestionJob, "source_id"),
        (models.ArticleAnalysisJob, "article_id"),
        (models.ArticleEnrichmentJob, "article_id"),
        (models.SourceEnrichmentJob, "source_id"),
        (models.ArticleImageJob, "article_id"),
        (models.ArticleImageJob, "topic_id"),
        (models.TopicAnalysisJob, "proposal_id"),
        (models.TopicAnalysisJob, "topic_id"),
    ],
)
def test_retry_selection_keeps_only_unresolved_latest_failure_even_with_out_of_order_active_run(
    model, subject
):
    # Execute the selection expression against minimal in-memory tables. Existing
    # PostgreSQL integration tests still verify real row locks and transactions.
    metadata = MetaData()
    names = ["id", "status", "created_at", "source_id", "article_id", "topic_id", "proposal_id"]
    table = Table(
        model.__tablename__,
        metadata,
        *[
            Column(name, model.__table__.c[name].type.copy())
            for name in names
            if name in model.__table__.c
        ],
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    base = {column.name: None for column in table.c}

    def row(number, owner, status="failed", age=0):
        return {
            **base,
            "id": uuid.UUID(int=number),
            subject: uuid.UUID(int=owner),
            "status": status,
            "created_at": NOW + timedelta(seconds=age),
        }

    rows = [
        row(1, 1),
        row(2, 1),  # ID breaks equal-timestamp ties.
        row(3, 2),
        row(4, 2, "succeeded", 1),
        row(5, 3),
        row(6, 3, "running", -1),  # Earlier active job still supersedes.
        row(7, 4),
        row(8, 4, "queued", -1),
        row(9, 5),  # Different subject remains retryable.
    ]
    with engine.begin() as connection:
        connection.execute(table.insert(), rows)
        eligible = set(
            connection.scalars(select(model.id).where(job_retries.retry_candidate(model)))
        )
        display = dict(
            connection.execute(select(model.id, job_retries.job_display_status(model))).all()
        )
    engine.dispose()
    assert eligible == {uuid.UUID(int=2), uuid.UUID(int=9)}
    assert [display[uuid.UUID(int=index)] for index in (1, 3, 5, 7)] == ["retried"] * 4
    assert display[uuid.UUID(int=6)] == "running"
    assert display[uuid.UUID(int=8)] == "queued"


def test_notification_retry_selection_reuses_each_failed_delivery():
    expression = job_retries.retry_candidate(models.NotificationDelivery)
    assert expression.compare(models.NotificationDelivery.status == "failed")


@pytest.mark.parametrize(
    "age,eligible",
    [
        (timedelta(), True),
        (timedelta(days=28) - timedelta(microseconds=1), True),
        (timedelta(days=28), False),
        (timedelta(days=29), False),
    ],
)
def test_notification_retry_keeps_identity_and_resets_the_lease_only_within_safe_window(
    monkeypatch, age, eligible
):
    monkeypatch.setattr(job_retries, "utcnow", lambda: NOW)
    delivery = models.NotificationDelivery(
        id=ID,
        status="failed",
        created_at=NOW - age,
        attempts=7,
        error="upstream failure",
        dispatched_at=NOW,
        finished_at=NOW,
        lease_until=NOW,
        lease_token=uuid.uuid4(),
    )
    session = Mock()
    session.scalar.return_value = delivery
    if not eligible:
        with pytest.raises(OperationConflict, match="safe retry window"):
            job_retries.retry_notification(session, ID)
        assert delivery.status == "failed" and delivery.attempts == 7
        session.flush.assert_not_called()
        return
    assert job_retries.retry_notification(session, ID) is delivery
    assert delivery.id == ID and delivery.status == "queued" and delivery.attempts == 0
    assert delivery.available_at == NOW
    assert all(
        getattr(delivery, field) is None
        for field in [
            "dispatched_at",
            "finished_at",
            "lease_until",
            "lease_token",
            "error",
        ]
    )
    session.flush.assert_called_once()


@pytest.mark.parametrize("status", [None, "queued", "running", "succeeded"])
def test_notification_retry_rejects_missing_or_nonfailed_delivery(status):
    session = Mock()
    session.scalar.return_value = None if status is None else SimpleNamespace(status=status)
    with pytest.raises(RecordNotFound if status is None else OperationConflict):
        job_retries.retry_notification(session, ID)
    session.flush.assert_not_called()


@pytest.mark.parametrize(
    "kind,model,function,field",
    [
        ("ingestion", models.IngestionJob, "retry_job", "id"),
        ("article-enrichment", models.ArticleEnrichmentJob, "retry_article", "id"),
        ("images", models.ArticleImageJob, "retry_image", "id"),
        ("source-enrichment", models.SourceEnrichmentJob, "request_enrichment", "source_id"),
        ("analysis", models.ArticleAnalysisJob, "request_analysis", "article_id"),
        ("topic-analysis", models.TopicAnalysisJob, "request_topic_analysis", "proposal_id"),
        ("notifications", models.NotificationDelivery, "retry_notification", "id"),
    ],
)
def test_failed_job_dispatches_to_the_correct_pipeline_and_subject(
    monkeypatch, kind, model, function, field
):
    monkeypatch.setattr(job_retries, "get_settings", lambda: SimpleNamespace(ai_enabled=True))
    values = {"id": ID, "status": "failed"}
    if field != "id":
        values[field] = uuid.UUID(int=20)
    job = model(**values)
    session = Mock()
    session.scalar.return_value = ID
    dispatch = Mock(return_value=object())
    monkeypatch.setattr(job_retries, function, dispatch)
    assert job_retries.retry_failed_job(session, job, kind, ACTOR) is dispatch.return_value
    args = (session, getattr(job, field))
    dispatch.assert_called_once_with(*args, *([ACTOR] if field == "proposal_id" else []))


def test_relationship_retry_retains_the_original_target(monkeypatch):
    monkeypatch.setattr(job_retries, "get_settings", lambda: SimpleNamespace(ai_enabled=True))
    target = uuid.UUID(int=21)
    job = models.TopicAnalysisJob(
        id=ID,
        status="failed",
        topic_id=uuid.UUID(int=20),
        input_snapshot={"related_topic_id": str(target)},
    )
    session = Mock()
    session.scalar.return_value = ID
    dispatch = Mock(return_value=object())
    monkeypatch.setattr(job_retries, "request_relationship_analysis", dispatch)
    assert (
        job_retries.retry_failed_job(session, job, "topic-analysis", ACTOR) is dispatch.return_value
    )
    args = dispatch.call_args.args
    assert args[0] is session and args[1] == job.topic_id and args[3] == ACTOR
    assert args[2].related_topic_id == target


@pytest.mark.parametrize(
    "reason", ["not-failed", "superseded", "ai-disabled", "image-present", "unsupported"]
)
def test_retry_does_not_admit_unsafe_or_unnecessary_work(monkeypatch, reason):
    monkeypatch.setattr(job_retries, "get_settings", lambda: SimpleNamespace(ai_enabled=False))
    job = models.ArticleImageJob(id=ID, status="queued" if reason == "not-failed" else "failed")
    session = Mock()
    session.scalar.return_value = None if reason == "superseded" else ID
    image_retry = Mock(return_value=None)
    monkeypatch.setattr(job_retries, "retry_image", image_retry)
    kind = (
        "analysis"
        if reason == "ai-disabled"
        else "unknown"
        if reason == "unsupported"
        else "images"
    )
    with pytest.raises(OperationConflict):
        job_retries.retry_failed_job(session, job, kind, ACTOR)
    if reason == "not-failed":
        session.scalar.assert_not_called()
    if reason != "image-present":
        image_retry.assert_not_called()

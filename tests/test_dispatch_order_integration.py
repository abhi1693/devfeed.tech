"""Exercise durable FIFO dispatch against real PostgreSQL and Redis queues."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.queue import get_queue
from devfeed_core.job_dispatch import DispatchLane, dispatch_jobs, dispatch_lanes
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    IngestionJob,
    Source,
    TopicAnalysisJob,
    TopicProposal,
    utcnow,
)
from rq.job import JobStatus
from sqlalchemy import select

pytestmark = pytest.mark.integration


def ingestion_jobs(database, count=3):
    now = utcnow()
    with database.begin() as session:
        ids = []
        for i in range(count):
            source = Source(
                name=str(i),
                feed_url=f"https://example.com/{i}",
                approval_status="approved",
                source_type="publisher",
            )
            session.add(source)
            session.flush()
            job = IngestionJob(source_id=source.id, available_at=now - timedelta(hours=count - i))
            session.add(job)
            session.flush()
            ids.append(str(job.id))
    return ids


def expire_dispatch_checks(database):
    with database.begin() as session:
        for job in session.scalars(select(IngestionJob)):
            job.dispatched_at = utcnow() - timedelta(minutes=6)


def test_waiting_deliveries_keep_their_place_and_do_not_expire_or_duplicate(database):
    ids = ingestion_jobs(database)
    queue = get_queue()
    assert dispatch_jobs(database, queue, 2, utcnow()) == 2
    original = queue.get_job_ids()
    assert [job.args[0] for job in queue.jobs] == ids[:2]
    assert all(queue.connection.ttl(job.key) == -1 for job in queue.jobs)
    expire_dispatch_checks(database)
    assert dispatch_jobs(database, queue, 3, utcnow()) == 3
    assert queue.get_job_ids()[:2] == original
    assert [job.args[0] for job in queue.jobs] == ids
    # Repeated recovery checks over a simulated long backlog retain FIFO.
    for _ in range(3):
        expire_dispatch_checks(database)
        dispatch_jobs(database, queue, 3, utcnow())
        assert [job.args[0] for job in queue.jobs] == ids
        assert all(queue.connection.ttl(job.key) == -1 for job in queue.jobs)
    queue.empty()
    expire_dispatch_checks(database)
    assert dispatch_jobs(database, queue, 3, utcnow()) == 3
    assert [job.args[0] for job in queue.jobs] == ids


def test_transport_failure_before_claim_does_not_block_recovery(database):
    ids = ingestion_jobs(database, 1)
    queue = get_queue()
    dispatch_jobs(database, queue, 1, utcnow())
    delivery = queue.jobs[0]
    queue.remove(delivery.id)
    delivery.set_status(JobStatus.FAILED)
    expire_dispatch_checks(database)
    assert dispatch_jobs(database, queue, 1, utcnow()) == 1
    assert [job.args[0] for job in queue.jobs] == ids
    assert queue.jobs[0].get_status() == JobStatus.QUEUED


def test_retry_gets_new_delivery_identity_and_respects_due_time(database):
    ids = ingestion_jobs(database, 1)
    queue = get_queue()
    now = utcnow()
    dispatch_jobs(database, queue, 1, now)
    original = queue.jobs[0]
    queue.remove(original.id)
    original.set_status(JobStatus.STARTED)
    with database.begin() as session:
        job = session.get(IngestionJob, uuid.UUID(ids[0]))
        job.attempts = 1
        job.available_at = now + timedelta(minutes=10)
        job.dispatched_at = None
    assert dispatch_jobs(database, queue, 1, now) == 0
    assert dispatch_jobs(database, queue, 1, now + timedelta(minutes=11)) == 1
    assert queue.jobs[0].id != original.id
    assert original.get_status() == JobStatus.STARTED  # Never overwrite an earlier execution.


def test_ai_queue_orders_topic_and_article_work_by_due_time_across_types(database):
    now = utcnow()
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="example",
            action="create",
            origin="import",
            source_name="Test",
            proposed={},
            evidence=[],
            created_by={},
        )
        article = Article(
            canonical_url="https://example.com/article", url_hash="order-test", title="Test"
        )
        session.add_all([proposal, article])
        session.flush()
        topic = TopicAnalysisJob(
            proposal_id=proposal.id,
            input_hash="a" * 64,
            input_snapshot={},
            requested_by={},
            prompt_version="1",
            available_at=now - timedelta(hours=6),
        )
        newer = ArticleAnalysisJob(article_id=article.id, available_at=now - timedelta(hours=1))
        session.add_all([topic, newer])
        session.flush()
        expected = [str(topic.id), str(newer.id)]
    queue = get_queue("analysis")
    lanes = [DispatchLane("analysis"), DispatchLane("topic-analysis", relationships=False)]
    counts = dispatch_lanes(database, queue, 1, now, lanes)
    assert counts == {"analysis": 0, "topic-analysis": 1}
    dispatch_lanes(database, queue, 1, now, lanes)
    assert [job.args[0] for job in queue.jobs] == expected

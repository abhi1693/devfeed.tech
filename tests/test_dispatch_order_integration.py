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


def test_orphaned_queued_hash_is_restored_once_without_reordering_waiters(database):
    ids = ingestion_jobs(database, 3)
    queue = get_queue()
    dispatch_jobs(database, queue, 3, utcnow())
    orphan = queue.jobs[0]
    queue.connection.lrem(queue.key, 0, orphan.id)
    waiting = queue.get_job_ids()
    assert orphan.get_status() == JobStatus.QUEUED
    expire_dispatch_checks(database)
    dispatch_jobs(database, queue, 3, utcnow())
    assert queue.get_job_ids() == [*waiting, orphan.id]
    expire_dispatch_checks(database)
    dispatch_jobs(database, queue, 3, utcnow())
    assert queue.get_job_ids() == [*waiting, orphan.id]
    assert sorted(job.args[0] for job in queue.jobs) == sorted(ids)


def test_intermediate_delivery_is_not_duplicated_by_reconciliation(database):
    ingestion_jobs(database, 1)
    queue = get_queue()
    dispatch_jobs(database, queue, 1, utcnow())
    identifier = queue.job_ids[0]
    queue.connection.lmove(queue.key, f"{queue.key}:intermediate", "LEFT", "RIGHT")
    expire_dispatch_checks(database)
    dispatch_jobs(database, queue, 1, utcnow())
    assert queue.job_ids == []
    assert queue.connection.lrange(f"{queue.key}:intermediate", 0, -1) == [identifier.encode()]


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


def test_dedicated_ai_queues_dispatch_their_eligible_job_types(database):
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
    article_queue = get_queue("article-analysis")
    topic_queue = get_queue("topic-analysis")
    article_counts = dispatch_lanes(database, article_queue, 1, now, [DispatchLane("analysis")])
    topic_counts = dispatch_lanes(
        database, topic_queue, 1, now, [DispatchLane("topic-analysis", relationships=False)]
    )
    assert article_counts == {"analysis": 1}
    assert topic_counts == {"topic-analysis": 1}
    assert [job.args[0] for job in article_queue.jobs] == expected[1:]
    assert [job.args[0] for job in topic_queue.jobs] == expected[:1]

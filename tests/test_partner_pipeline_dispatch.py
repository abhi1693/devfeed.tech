"""Bounded, fair publication of catalog work to real Redis queues."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_aggregator.partner_tasks import dispatch_partner_evaluations
from devfeed_aggregator.queue import get_queue
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerConnection,
    PartnerEvaluation,
    PartnerPipelineJob,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_catalog import lock_catalog
from sqlalchemy import func, insert, select, text

pytestmark = pytest.mark.integration


def seed_jobs(database, count, *, operation="sync_product", **values):
    now = utcnow()
    identifiers = [uuid.uuid4() for _ in range(count)]
    with database.begin() as session:
        if session.get(PartnerConnection, "dispatch") is None:
            session.add(
                PartnerConnection(
                    provider="dispatch", enabled=True, next_sync_at=now + timedelta(days=1)
                )
            )
            session.flush()
        products = [uuid.uuid4() for _ in identifiers] if operation == "assess" else []
        if products:
            session.execute(
                insert(PartnerProduct),
                [
                    {
                        "id": identifier,
                        "name": f"Tool {index}",
                        "product_url": f"https://dispatch-{identifier}.example/",
                        "description": "A developer tool awaiting qualification.",
                        "assessment_revision": 1,
                    }
                    for index, identifier in enumerate(products)
                ],
            )
        session.execute(
            insert(PartnerPipelineJob),
            [
                {
                    "id": identifier,
                    "provider": None if operation == "assess" else "dispatch",
                    "operation": operation,
                    "product_id": products[index] if products else None,
                    "created_at": now - timedelta(hours=1),
                    "available_at": now - timedelta(minutes=1),
                    **values,
                }
                for index, identifier in enumerate(identifiers)
            ],
        )
    return identifiers


def published_count(database):
    with database() as session:
        return session.scalar(
            select(func.count())
            .select_from(PartnerPipelineJob)
            .where(PartnerPipelineJob.dispatched_at.is_not(None))
        )


def queue_identifiers(name):
    queue = get_queue(name)
    try:
        return {uuid.UUID(job.args[0]) for job in queue.jobs}
    finally:
        queue.connection.close()


def test_dispatch_only_publishes_due_work_and_expired_leases(database):
    now = utcnow()
    due = seed_jobs(database, 1)[0]
    expired = seed_jobs(database, 1, status="running", lease_until=now - timedelta(seconds=1))[0]
    seed_jobs(database, 1, available_at=now + timedelta(minutes=1))
    seed_jobs(database, 1, status="running", lease_until=now + timedelta(minutes=1))
    seed_jobs(database, 1, status="failed")
    seed_jobs(database, 1, status="succeeded")

    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 2
    assert queue_identifiers("ingestion") == {due, expired}
    assert published_count(database) == 2


def test_large_import_refills_bounded_queue_without_two_job_throttle(database, monkeypatch):
    identifiers = set(seed_jobs(database, 6000))
    batch = get_settings().scheduler_batch_size
    now = utcnow()
    monkeypatch.setattr(pipeline, "utcnow", lambda: now)

    assert batch >= 100
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == 0
    assert published_count(database) == 2 * batch

    queue = get_queue("ingestion")
    try:
        assert queue.count == 2 * batch
        assert queue_identifiers("ingestion") <= identifiers
        # Workers remove deliveries from the waiting list before processing them.
        # Retain their RQ hashes, as genuine running deliveries would.
        consumed = set()

        def consume(count):
            removed = {uuid.UUID(job.args[0]) for job in queue.get_jobs(0, count)}
            assert len(removed) == count
            assert removed.isdisjoint(consumed)
            queue.connection.ltrim(queue.key, count, -1)
            consumed.update(removed)

        consume(batch)
        assert len(consumed) == batch
        assert pipeline.dispatch_partner_pipeline(database) == batch
        assert queue.count == 2 * batch
        assert published_count(database) == 3 * batch
        assert queue_identifiers("ingestion").isdisjoint(consumed)
        published = 3 * batch
        while published < 6000:
            consume(batch)
            following = min(batch, 6000 - published)
            assert pipeline.dispatch_partner_pipeline(database) == following
            assert queue.count == batch + following
            published += following
        consume(queue.count)
        assert consumed == identifiers
        assert published_count(database) == 6000
        assert pipeline.dispatch_partner_pipeline(database) == 0
    finally:
        queue.connection.close()


def test_network_backlog_does_not_starve_qualification(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    batch = get_settings().scheduler_batch_size
    network = set(seed_jobs(database, 600))
    assessment = set(seed_jobs(database, 300, operation="assess", created_at=utcnow()))

    assert pipeline.dispatch_partner_pipeline(database) == 2 * batch
    assert len(queue_identifiers("ingestion")) == batch
    assert len(queue_identifiers("source-analysis")) == batch
    assert queue_identifiers("ingestion") <= network
    assert queue_identifiers("source-analysis") <= assessment


def test_full_network_queue_still_allows_qualification(database, monkeypatch):
    batch = get_settings().scheduler_batch_size
    seed_jobs(database, 4 * batch)
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == 0

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    assessment = set(seed_jobs(database, batch, operation="assess"))
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert len(queue_identifiers("ingestion")) == 2 * batch
    assert queue_identifiers("source-analysis") == assessment


def test_evaluations_use_full_batch_and_share_analysis_queue_ceiling(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    batch = get_settings().scheduler_batch_size
    identifiers = seed_jobs(database, 2 * batch, operation="assess")
    with database.begin() as session:
        products = session.scalars(
            select(PartnerPipelineJob.product_id).where(PartnerPipelineJob.id.in_(identifiers))
        ).all()
        session.execute(
            insert(PartnerEvaluation),
            [
                {"product_id": identifier, "snapshot": {}, "requested_by": {}}
                for identifier in products
            ],
        )

    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert dispatch_partner_evaluations(database) == batch
    assert len(queue_identifiers("source-analysis")) == 2 * batch
    assert pipeline.dispatch_partner_pipeline(database) == 0
    assert dispatch_partner_evaluations(database) == 0
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerEvaluation)
                .where(PartnerEvaluation.dispatched_at.is_not(None))
            )
            == batch
        )


def test_disabled_ai_leaves_assessments_unpublished_without_blocking_imports(database):
    network = set(seed_jobs(database, 5))
    seed_jobs(database, 5, operation="assess", created_at=utcnow() - timedelta(days=1))

    assert not get_settings().ai_enabled
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 5
    assert queue_identifiers("ingestion") == network
    assert queue_identifiers("source-analysis") == set()


def test_publishers_share_queue_ceiling_without_duplicate_deliveries(database):
    identifiers = set(seed_jobs(database, 600))
    batch = get_settings().scheduler_batch_size
    barrier = Barrier(4)

    def dispatch():
        barrier.wait(timeout=10)
        return pipeline.dispatch_partner_pipeline(database)

    with ThreadPoolExecutor(max_workers=4) as executor:
        counts = list(executor.map(lambda _: dispatch(), range(4)))

    assert sum(counts) <= 2 * batch
    assert sum(counts) >= batch
    assert published_count(database) == sum(counts)
    queue = get_queue("ingestion")
    try:
        assert queue.count == len(queue_identifiers("ingestion")) == sum(counts)
        assert queue_identifiers("ingestion") <= identifiers
    finally:
        queue.connection.close()


def test_redis_publication_does_not_hold_catalog_lock(database, monkeypatch):
    seed_jobs(database, 3)
    queue = get_queue("ingestion")
    original = queue.enqueue
    observed = []

    def enqueue(*args, **kwargs):
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '500ms'"))
            lock_catalog(session)
            observed.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue", enqueue)
    monkeypatch.setattr(pipeline, "get_queue", lambda _: queue)
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 3
    assert len(observed) == 3


def test_fresh_work_and_delivery_rechecks_both_progress(database):
    old = set(seed_jobs(database, 5))
    assert pipeline.dispatch_partner_pipeline(database, limit=5) == 5
    with database.begin() as session:
        for identifier in old:
            session.get(PartnerPipelineJob, identifier).dispatched_at = utcnow() - timedelta(
                seconds=61
            )
    new = set(seed_jobs(database, 5))

    assert pipeline.dispatch_partner_pipeline(database, limit=5) == 5
    assert published_count(database) == 9
    assert len(queue_identifiers("ingestion") & new) == 4
    assert pipeline.dispatch_partner_pipeline(database, limit=5) >= 1
    assert published_count(database) == 10
    assert queue_identifiers("ingestion") == old | new


def test_lost_delivery_recovers_while_fresh_backlog_remains(database):
    lost = seed_jobs(database, 1)[0]
    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    queue = get_queue("ingestion")
    try:
        delivery = queue.jobs[0]
        assert queue.connection.lrem(queue.key, 0, delivery.id) == 1
        with database.begin() as session:
            session.get(PartnerPipelineJob, lost).dispatched_at = utcnow() - timedelta(seconds=61)
        fresh = set(seed_jobs(database, 200))

        assert pipeline.dispatch_partner_pipeline(database, limit=5) == 5
        queued = queue_identifiers("ingestion")
        assert lost in queued
        assert len(queued & fresh) == 4
        assert len(queued) == queue.count == 5
        assert published_count(database) == 5
    finally:
        queue.connection.close()


def test_single_slot_refills_progress_fresh_work_and_lost_deliveries(database):
    old = set(seed_jobs(database, 4))
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 4
    queue = get_queue("ingestion")
    try:
        assert queue.connection.delete(queue.key) == 1
        with database.begin() as session:
            for identifier in old:
                session.get(PartnerPipelineJob, identifier).dispatched_at = utcnow() - timedelta(
                    seconds=61
                )
        fresh = set(seed_jobs(database, 200))

        for _ in range(4):
            assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
        queued = queue_identifiers("ingestion")
        assert len(queued & old) == 1
        assert len(queued & fresh) == 3
        assert len(queued) == queue.count == 4
        assert published_count(database) == 7
    finally:
        queue.connection.close()


def test_failed_publication_retries_without_duplicate_redis_deliveries(database, monkeypatch):
    identifiers = set(seed_jobs(database, 4))
    queue = get_queue("ingestion")
    original = queue.enqueue
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Redis temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "enqueue", fail_second)
    monkeypatch.setattr(pipeline, "get_queue", lambda _: queue)
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        pipeline.dispatch_partner_pipeline(database, limit=10)
    assert queue.count == 1

    monkeypatch.setattr(queue, "enqueue", original)
    pipeline.dispatch_partner_pipeline(database, limit=10)
    assert queue.count == 4
    assert queue_identifiers("ingestion") == identifiers
    assert published_count(database) == 4


def test_network_publication_failure_still_allows_qualification(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    seed_jobs(database, 3)
    assessment = set(seed_jobs(database, 3, operation="assess"))
    network = get_queue("ingestion")
    lookup = get_queue

    def fail(*_, **__):
        raise RuntimeError("Import queue temporarily unavailable")

    monkeypatch.setattr(network, "enqueue", fail)
    monkeypatch.setattr(
        pipeline, "get_queue", lambda name: network if name == "ingestion" else lookup(name)
    )
    with pytest.raises(RuntimeError, match="Import queue temporarily unavailable"):
        pipeline.dispatch_partner_pipeline(database, limit=10)

    assert queue_identifiers("ingestion") == set()
    assert queue_identifiers("source-analysis") == assessment
    assert published_count(database) == 3


def test_discovery_completion_waits_for_children_without_occupying_dispatch_slots(database):
    parent = seed_jobs(database, 1, operation="sync", payload={"discovery_complete": True})[0]
    child = seed_jobs(database, 1, parent_id=parent)[0]

    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    assert queue_identifiers("ingestion") == {child}
    with database.begin() as session:
        session.get(PartnerPipelineJob, child).status = "succeeded"
    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    assert queue_identifiers("ingestion") == {child, parent}


def test_refill_can_publish_without_rescanning_catalog(database, monkeypatch):
    identifiers = set(seed_jobs(database, 5))
    monkeypatch.setattr(
        pipeline,
        "schedule_partner_syncs",
        lambda _: pytest.fail("Queue refills must not rescan connection and product enrollment"),
    )

    assert pipeline.dispatch_partner_pipeline(database, schedule=False) == 5
    assert queue_identifiers("ingestion") == identifiers


def test_republishing_due_delivery_restores_missing_queue_membership(database):
    identifier = seed_jobs(database, 1)[0]
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 1
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 0
    queue = get_queue("ingestion")
    try:
        delivery = queue.jobs[0]
        assert queue.connection.lrem(queue.key, 0, delivery.id) == 1
        assert queue.count == 0
        with database.begin() as session:
            job = session.get(PartnerPipelineJob, identifier)
            job.dispatched_at = utcnow() - timedelta(seconds=61)
        assert pipeline.dispatch_partner_pipeline(database, limit=10) == 1
        assert queue_identifiers("ingestion") == {identifier}
        assert queue.count == 1
    finally:
        queue.connection.close()

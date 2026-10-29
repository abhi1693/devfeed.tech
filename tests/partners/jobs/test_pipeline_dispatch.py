"""Bounded, fair publication of catalog work to real Redis queues."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

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

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("priority_queue_routes")]


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
                        "tagline": "A developer tool awaiting qualification.",
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


def queue_identifiers(*, ai=False):
    queue = get_queue("partner-pipeline")
    try:
        assert all(
            job.meta.get("devfeed_queue") in {"partner-pipeline", "partner-evaluation"}
            for job in queue.jobs
        )
        return {uuid.UUID(job.args[0]) for job in queue.jobs if job.meta.get("devfeed_ai") is ai}
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
    assert queue_identifiers() == {due, expired}
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

    queue = get_queue("partner-pipeline")
    try:
        assert queue.count == 2 * batch
        assert queue_identifiers() <= identifiers
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
        assert queue_identifiers().isdisjoint(consumed)
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
    assert len(queue_identifiers()) == batch
    assert len(queue_identifiers(ai=True)) == batch
    assert queue_identifiers() <= network
    assert queue_identifiers(ai=True) <= assessment


def test_shared_queue_bounds_publication_and_refills_both_stages_after_consumption(
    database, monkeypatch
):
    batch = get_settings().scheduler_batch_size
    seed_jobs(database, 4 * batch)
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == 0

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    assessment = set(seed_jobs(database, batch, operation="assess"))
    assert pipeline.dispatch_partner_pipeline(database) == 0
    assert len(queue_identifiers()) == 2 * batch
    assert queue_identifiers(ai=True) == set()
    queue = get_queue("partner-pipeline")
    try:
        # Waiting sync and assessment deliveries share one bounded physical queue.
        queue.connection.ltrim(queue.key, 2 * batch, -1)
        assert pipeline.dispatch_partner_pipeline(database) == 2 * batch
        assert queue.count == 2 * batch
    finally:
        queue.connection.close()
    assert len(queue_identifiers()) == batch
    assert queue_identifiers(ai=True) == assessment


@pytest.mark.parametrize("destination", ["default", "catalog-pipeline"])
def test_one_slot_refills_alternate_stages_without_burning_turns_on_full_polls(
    database, monkeypatch, destination
):
    settings = get_settings()
    monkeypatch.setattr(settings, "job_queue_overrides", {"partner-pipeline": destination})
    batch = settings.scheduler_batch_size
    seed_jobs(database, 4 * batch)
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == batch
    monkeypatch.setattr(settings, "ai_enabled", True)
    seed_jobs(database, batch, operation="assess")
    queue = get_queue("partner-pipeline")
    try:
        choices = []
        for _ in range(8):
            assert pipeline.dispatch_partner_pipeline(database, limit=1) == 0
            assert queue.connection.lpop(queue.key) is not None
            assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
            assert queue.count == 2 * batch
            choices.append(queue.jobs[-1].meta["devfeed_ai"])
            assert pipeline.dispatch_partner_pipeline(database, limit=1) == 0
        assert choices == [False, True] * 4
    finally:
        queue.connection.close()


def test_ai_pipeline_refill_uses_one_database_connection(database, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    seed_jobs(database, 1)
    seed_jobs(database, 1, operation="assess")
    monkeypatch.setattr(settings, "ai_enabled", True)
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        assert pipeline.dispatch_partner_pipeline(factory, limit=1) == 2
        assert published_count(database) == 2
    finally:
        engine.dispose()


def test_concurrent_job_kinds_share_capacity_and_pipeline_lock_losers_keep_the_next_turn(
    database, monkeypatch
):
    settings = get_settings()
    batch = settings.scheduler_batch_size
    seed_jobs(database, 4 * batch)
    assert pipeline.dispatch_partner_pipeline(database) == batch
    assert pipeline.dispatch_partner_pipeline(database) == batch
    identifiers = seed_jobs(database, batch, operation="assess")
    with database.begin() as session:
        products = session.scalars(
            select(PartnerPipelineJob.product_id).where(PartnerPipelineJob.id.in_(identifiers))
        ).all()
        session.add_all(
            PartnerEvaluation(product_id=product, snapshot={}, requested_by={})
            for product in products
        )
    monkeypatch.setattr(settings, "ai_enabled", True)
    entered, release = Event(), Event()

    def lookup(name):
        current = get_queue(name)
        original = current.enqueue

        def enqueue(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(timeout=10)
            return original(*args, **kwargs)

        current.enqueue = enqueue
        return current

    monkeypatch.setattr(pipeline, "get_queue", lookup)
    queue = get_queue("partner-pipeline")
    try:
        assert queue.connection.lpop(queue.key) is not None
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(pipeline.dispatch_partner_pipeline, database, limit=1)
            try:
                assert entered.wait(timeout=10)
                assert pipeline.dispatch_partner_pipeline(database, limit=1) == 0
                assert dispatch_partner_evaluations(database, limit=1) == 0
            finally:
                release.set()
            assert future.result(timeout=10) == 1
        assert queue.count == 2 * batch
        assert queue.connection.lpop(queue.key) is not None
        assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
        assert queue.count == 2 * batch
        assert queue.jobs[-1].meta["devfeed_ai"] is True
    finally:
        queue.connection.close()


def test_evaluations_use_full_batch_and_share_physical_queue_ceiling(database, monkeypatch):
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
    assert len(queue_identifiers(ai=True)) == 2 * batch
    queue = get_queue("partner-pipeline")
    try:
        assert queue.name == "default"
        assert {job.meta["devfeed_queue"] for job in queue.jobs} == {
            "partner-pipeline",
            "partner-evaluation",
        }
    finally:
        queue.connection.close()
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


def test_explicit_partner_overrides_isolate_pipeline_and_evaluation_job_kinds(
    database, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(
        settings,
        "job_queue_overrides",
        {"partner-pipeline": "catalog-pipeline", "partner-evaluation": "catalog-matches"},
    )
    seed_jobs(database, 1)
    identifiers = seed_jobs(database, 1, operation="assess")
    with database.begin() as session:
        product = session.scalar(
            select(PartnerPipelineJob.product_id).where(PartnerPipelineJob.id == identifiers[0])
        )
        session.add(PartnerEvaluation(product_id=product, snapshot={}, requested_by={}))
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 2
    assert dispatch_partner_evaluations(database, limit=10) == 1
    sync_queue = get_queue("partner-pipeline")
    match_queue = get_queue("partner-evaluation")
    try:
        assert sync_queue.name == "catalog-pipeline" and match_queue.name == "catalog-matches"
        assert [job.meta["devfeed_ai"] for job in sync_queue.jobs] == [False, True]
        assert all(job.meta["devfeed_queue"] == "partner-pipeline" for job in sync_queue.jobs)
        assert len(match_queue.jobs) == 1
        assert match_queue.jobs[0].meta["devfeed_queue"] == "partner-evaluation"
        assert match_queue.jobs[0].meta["devfeed_ai"] is True
    finally:
        sync_queue.connection.close()
        match_queue.connection.close()


def test_disabled_ai_leaves_assessments_unpublished_without_blocking_imports(database):
    network = set(seed_jobs(database, 5))
    seed_jobs(database, 5, operation="assess", created_at=utcnow() - timedelta(days=1))

    assert not get_settings().ai_enabled
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 5
    assert queue_identifiers() == network
    assert queue_identifiers(ai=True) == set()


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
    queue = get_queue("partner-pipeline")
    try:
        assert queue.count == len(queue_identifiers()) == sum(counts)
        assert queue_identifiers() <= identifiers
    finally:
        queue.connection.close()


def test_redis_publication_does_not_hold_catalog_lock(database, monkeypatch):
    seed_jobs(database, 3)
    queue = get_queue("partner-pipeline")
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
    assert len(queue_identifiers() & new) == 4
    assert pipeline.dispatch_partner_pipeline(database, limit=5) >= 1
    assert published_count(database) == 10
    assert queue_identifiers() == old | new


def test_lost_delivery_recovers_while_fresh_backlog_remains(database):
    lost = seed_jobs(database, 1)[0]
    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    queue = get_queue("partner-pipeline")
    try:
        delivery = queue.jobs[0]
        assert queue.connection.lrem(queue.key, 0, delivery.id) == 1
        with database.begin() as session:
            session.get(PartnerPipelineJob, lost).dispatched_at = utcnow() - timedelta(seconds=61)
        fresh = set(seed_jobs(database, 200))

        assert pipeline.dispatch_partner_pipeline(database, limit=5) == 5
        queued = queue_identifiers()
        assert lost in queued
        assert len(queued & fresh) == 4
        assert len(queued) == queue.count == 5
        assert published_count(database) == 5
    finally:
        queue.connection.close()


def test_single_slot_refills_progress_fresh_work_and_lost_deliveries(database):
    old = set(seed_jobs(database, 4))
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 4
    queue = get_queue("partner-pipeline")
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
        queued = queue_identifiers()
        assert len(queued & old) == 1
        assert len(queued & fresh) == 3
        assert len(queued) == queue.count == 4
        assert published_count(database) == 7
    finally:
        queue.connection.close()


def test_failed_publication_retries_without_duplicate_redis_deliveries(database, monkeypatch):
    identifiers = set(seed_jobs(database, 4))
    queue = get_queue("partner-pipeline")
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
    assert queue_identifiers() == identifiers
    assert published_count(database) == 4


def test_network_publication_failure_still_allows_qualification(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    seed_jobs(database, 3)
    assessment = set(seed_jobs(database, 3, operation="assess"))
    network = get_queue("partner-pipeline")
    original = network.enqueue

    def fail_network(*args, **kwargs):
        if not kwargs["meta"]["devfeed_ai"]:
            raise RuntimeError("Import publication temporarily unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(network, "enqueue", fail_network)
    monkeypatch.setattr(pipeline, "get_queue", lambda _: network)
    with pytest.raises(RuntimeError, match="Import publication temporarily unavailable"):
        pipeline.dispatch_partner_pipeline(database, limit=10)

    assert queue_identifiers() == set()
    assert queue_identifiers(ai=True) == assessment
    assert published_count(database) == 3


def test_discovery_completion_waits_for_children_without_occupying_dispatch_slots(database):
    parent = seed_jobs(database, 1, operation="sync", payload={"discovery_complete": True})[0]
    child = seed_jobs(database, 1, parent_id=parent)[0]

    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    assert queue_identifiers() == {child}
    with database.begin() as session:
        session.get(PartnerPipelineJob, child).status = "succeeded"
    assert pipeline.dispatch_partner_pipeline(database, limit=1) == 1
    assert queue_identifiers() == {child, parent}


def test_refill_can_publish_without_rescanning_catalog(database, monkeypatch):
    identifiers = set(seed_jobs(database, 5))
    monkeypatch.setattr(
        pipeline,
        "schedule_partner_syncs",
        lambda _: pytest.fail("Queue refills must not rescan connection and product enrollment"),
    )

    assert pipeline.dispatch_partner_pipeline(database, schedule=False) == 5
    assert queue_identifiers() == identifiers


def test_republishing_due_delivery_restores_missing_queue_membership(database):
    identifier = seed_jobs(database, 1)[0]
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 1
    assert pipeline.dispatch_partner_pipeline(database, limit=10) == 0
    queue = get_queue("partner-pipeline")
    try:
        delivery = queue.jobs[0]
        assert queue.connection.lrem(queue.key, 0, delivery.id) == 1
        assert queue.count == 0
        with database.begin() as session:
            job = session.get(PartnerPipelineJob, identifier)
            job.dispatched_at = utcnow() - timedelta(seconds=61)
        assert pipeline.dispatch_partner_pipeline(database, limit=10) == 1
        assert queue_identifiers() == {identifier}
        assert queue.count == 1
    finally:
        queue.connection.close()

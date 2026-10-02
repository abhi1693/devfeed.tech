"""Per-product commits, durable checkpoints, and failure isolation."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline
from devfeed_core.models import (
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_tools import ProductInput
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload
from test_partner_tools_integration import CONNECTION, connect, finish_sync, job_id, synced

product_payload = shared_product_payload
pytestmark = pytest.mark.integration


def items(payload, count=3):
    return [
        ProductInput.model_validate(
            {
                **payload,
                "external_id": f"tool-{n}",
                "name": f"Tool {n}",
                "product_url": f"https://tool{n}.example/",
            }
        )
        for n in range(count)
    ]


def discover(client, database, payload):
    connect(client)
    identifier = job_id(database, "sync")
    process_pipeline(identifier, factory=database, reader=lambda _: (items(payload), None))
    with database() as session:
        children = session.scalars(
            select(PartnerPipelineJob)
            .where(PartnerPipelineJob.parent_id == uuid.UUID(identifier))
            .order_by(PartnerPipelineJob.external_id)
        ).all()
        return identifier, [str(child.id) for child in children]


def due(database, identifier):
    with database.begin() as session:
        session.get(PartnerPipelineJob, uuid.UUID(identifier)).available_at = utcnow() - timedelta(
            seconds=1
        )


def product_count(database):
    with database() as session:
        return session.scalar(select(func.count()).select_from(PartnerProduct))


def test_product_db_failure_retries_only_that_product(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_aggregator import partner_product_sync

    parent, children = discover(admin_client, database, product_payload)
    assert len(children) == 3 and product_count(database) == 0
    process_pipeline(children[0], factory=database)
    assert product_count(database) == 1
    original = partner_product_sync.upsert_partner_product

    def fail_after_write(session, item, generation):
        original(session, item, generation)
        session.flush()
        raise RuntimeError("Simulated product database failure")

    monkeypatch.setattr(partner_product_sync, "upsert_partner_product", fail_after_write)
    process_pipeline(children[1], factory=database)
    assert product_count(database) == 1  # The failed product transaction was rolled back.
    monkeypatch.setattr(partner_product_sync, "upsert_partner_product", original)
    process_pipeline(children[2], factory=database)
    assert product_count(database) == 2
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(children[1])).status == "queued"
        assert session.get(PartnerConnection, "nick-launches").last_sync_at is None
    due(database, children[1])
    finish_sync(database, parent)
    assert product_count(database) == 3
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(parent)).status == "succeeded"
        assert [session.get(PartnerPipelineJob, uuid.UUID(i)).attempts for i in children] == [
            1,
            2,
            1,
        ]
    process_pipeline(
        children[0],
        factory=database,
        product_reader=lambda *_: pytest.fail("Completed product rerun"),
    )


def test_failed_product_blocks_withdrawal_and_manual_retry_resumes_it(
    admin_client, database, product_payload
):
    old = synced(admin_client, database, product_payload)
    parent, children = discover(admin_client, database, product_payload)
    process_pipeline(children[0], factory=database)
    process_pipeline(
        children[1],
        factory=database,
        product_reader=lambda *_: (_ for _ in ()).throw(ValueError("Invalid product")),
    )
    process_pipeline(children[2], factory=database)
    due(database, parent)
    process_pipeline(parent, factory=database)
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(parent)).status == "failed"
        assert session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == uuid.UUID(old["id"]))
        ).active
    assert admin_client.post(CONNECTION, json={"action": "sync"}).status_code == 200
    assert job_id(database, "sync") == parent
    # No page refetch and no successful-product reruns on resumption.
    process_pipeline(parent, factory=database, reader=lambda _: pytest.fail("Discovery rerun"))
    finish_sync(database, parent)
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(parent)).status == "succeeded"
        assert [session.get(PartnerPipelineJob, uuid.UUID(i)).attempts for i in children] == [
            1,
            1,
            1,
        ]
        assert not session.scalar(
            select(PartnerListing).where(PartnerListing.product_id == uuid.UUID(old["id"]))
        ).active


def test_page_checkpoint_deduplicates_repeated_product(admin_client, database, product_payload):
    connect(admin_client)
    parent = job_id(database, "sync")
    item = items(product_payload, 1)[0]
    process_pipeline(parent, factory=database, reader=lambda _: ([item, item], "page-2"))
    with database() as session:
        child = str(
            session.scalar(
                select(PartnerPipelineJob.id).where(
                    PartnerPipelineJob.parent_id == uuid.UUID(parent)
                )
            )
        )
    process_pipeline(child, factory=database)
    due(database, parent)

    def next_page(cursor):
        assert cursor == "page-2"
        return [item], None

    process_pipeline(parent, factory=database, reader=next_page)
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPipelineJob)
                .where(PartnerPipelineJob.parent_id == uuid.UUID(parent))
            )
            == 1
        )
        assert session.get(PartnerPipelineJob, uuid.UUID(child)).attempts == 1
        assert session.get(PartnerPipelineJob, uuid.UUID(parent)).status == "succeeded"


def test_worker_crash_recovers_only_expired_product_lease(admin_client, database, product_payload):
    _, children = discover(admin_client, database, product_payload)
    with pytest.raises(SystemExit):
        process_pipeline(
            children[0],
            factory=database,
            product_reader=lambda *_: (_ for _ in ()).throw(SystemExit()),
        )
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(children[0]))
        assert job.status == "running"
        job.lease_until = utcnow() - timedelta(seconds=1)
    process_pipeline(children[0], factory=database)
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(children[0])).attempts == 2
        assert session.get(PartnerPipelineJob, uuid.UUID(children[1])).attempts == 0
    assert product_count(database) == 1


def test_pause_fences_product_result(admin_client, database, product_payload):
    _, children = discover(admin_client, database, product_payload)

    def pause(provider, candidate):
        assert admin_client.post(CONNECTION, json={"action": "pause"}).status_code == 200
        return ProductInput.model_validate(candidate.data)

    process_pipeline(children[0], factory=database, product_reader=pause)
    assert product_count(database) == 0
    with database() as session:
        assert all(
            session.get(PartnerPipelineJob, uuid.UUID(i)).status == "failed" for i in children
        )


def test_dispatches_product_jobs_without_ai_or_waiting_parent(
    admin_client, database, product_payload
):
    from devfeed_aggregator.partner_sync_tasks import dispatch_partner_pipeline
    from devfeed_aggregator.queue import get_queue

    parent, children = discover(admin_client, database, product_payload)
    due(database, parent)
    assert dispatch_partner_pipeline(database, limit=10) == 3
    queue = get_queue("ingestion")
    try:
        assert {job.args[0] for job in queue.jobs} == set(children)
    finally:
        queue.connection.close()


@pytest.mark.parametrize("operation", ["sync", "sync_product"])
def test_background_worker_recovers_delivery_on_old_discovery_queue(
    admin_client, database, product_payload, monkeypatch, operation
):
    from contextlib import ExitStack

    from devfeed_aggregator import partner_sync_tasks
    from devfeed_aggregator.queue import get_queue
    from devfeed_core.worker_queues import worker_queues
    from rq import SimpleWorker
    from rq.serializers import JSONSerializer

    if operation == "sync":
        connect(admin_client)
        identifier = job_id(database, "sync")
        monkeypatch.setattr(partner_sync_tasks, "read_partner_page", lambda *_: ([], None))
    else:
        _, children = discover(admin_client, database, product_payload)
        identifier = children[0]
    with ExitStack() as stack:
        old_queue = get_queue("source-discovery")
        stack.callback(old_queue.connection.close)
        old_queue.enqueue(
            "devfeed_aggregator.partner_sync_tasks.process_pipeline",
            identifier,
            job_id=f"partner-pipeline-{identifier}-0-0-0",
            unique=True,
            result_ttl=0,
        )
        with database.begin() as session:
            job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
            job.dispatched_at = utcnow() - timedelta(seconds=61)
        assert partner_sync_tasks.dispatch_partner_pipeline(database, limit=10) >= 1
        queues = [
            get_queue(name)
            for name in worker_queues("background", ai_enabled=False, notifications_enabled=False)
        ]
        for queue in queues:
            stack.callback(queue.connection.close)
        # Consume with the actual default worker subscriptions, without the
        # optional source-discovery service. Network reads use fixture data.
        SimpleWorker(queues, connection=queues[0].connection, serializer=JSONSerializer).work(
            burst=True
        )
        with database() as session:
            job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
            assert job.status == "succeeded" and job.attempts == 1
        # A later consumer of the stale delivery must not repeat completed work.
        SimpleWorker([old_queue], connection=old_queue.connection, serializer=JSONSerializer).work(
            burst=True
        )
        with database() as session:
            job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
            assert job.status == "succeeded" and job.attempts == 1


def test_failed_discovery_resumes_saved_page_without_repeating_commits(
    admin_client, database, product_payload
):
    from devfeed_core.feeds.fetcher import FeedError

    connect(admin_client)
    parent = job_id(database, "sync")
    first, second = items(product_payload, 2)
    process_pipeline(parent, factory=database, reader=lambda _: ([first], "next-page"))
    with database() as session:
        child = str(
            session.scalar(
                select(PartnerPipelineJob.id).where(
                    PartnerPipelineJob.parent_id == uuid.UUID(parent)
                )
            )
        )
    due(database, parent)
    process_pipeline(
        parent,
        factory=database,
        reader=lambda _: (_ for _ in ()).throw(FeedError("failed", status=403, retryable=False)),
    )
    # Product work from a discovered page is independent of the next page's failure.
    process_pipeline(child, factory=database)
    assert product_count(database) == 1
    assert admin_client.post(CONNECTION, json={"action": "sync"}).status_code == 200
    assert job_id(database, "sync") == parent

    def resume(cursor):
        assert cursor == "next-page"
        return [second], None

    process_pipeline(parent, factory=database, reader=resume)
    finish_sync(database, parent)
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(child)).attempts == 1
    assert product_count(database) == 2

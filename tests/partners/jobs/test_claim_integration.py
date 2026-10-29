"""A slow publisher cannot make a waiting worker retain the catalog lock."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from devfeed_aggregator import partner_claim, partner_sync_tasks
from devfeed_aggregator.queue import get_queue
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import PartnerConnection, PartnerEvaluation, PartnerPipelineJob, utcnow
from devfeed_core.partner_tools import ProductInput
from rq import SimpleWorker
from rq.serializers import JSONSerializer
from sqlalchemy import select

from tests.partners.catalog.test_tools import product_payload as product_payload
from tests.partners.catalog.test_tools_integration import (
    answer,
    article,
    connect,
    job_id,
    qualified,
)
from tests.partners.jobs.test_product_sync import discover
from tests.support.partner_claims import paused_claim_retry

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("priority_queue_routes")]


@pytest.mark.parametrize("contended", ["connection", "job"])
def test_pipeline_releases_catalog_while_waiting_for_connection_or_job(
    admin_client, database, monkeypatch, contended
):
    connect(admin_client)
    identifier = job_id(database, "sync")
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        paused_claim_retry(monkeypatch, database) as claim,
    ):
        with database.begin() as publisher:
            statement = (
                select(PartnerConnection).where(PartnerConnection.provider == "nick-launches")
                if contended == "connection"
                else select(PartnerPipelineJob).where(
                    PartnerPipelineJob.id == uuid.UUID(identifier)
                )
            )
            publisher.scalar(statement.with_for_update())
            worker = pool.submit(
                partner_sync_tasks.process_pipeline,
                identifier,
                factory=database,
                reader=lambda _: ([], None),
            )
            claim.assert_waiting(worker)
        claim.resume()
        assert worker.result(timeout=5) is None
    with database() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        assert job.status == "succeeded" and job.attempts == 1


def test_product_claim_releases_catalog_until_publisher_commits(
    admin_client, database, product_payload, monkeypatch
):
    _parent, children = discover(admin_client, database, product_payload)
    identifier = children[0]
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        paused_claim_retry(monkeypatch, database) as claim,
    ):
        with database.begin() as publisher:
            publisher.scalar(
                select(PartnerPipelineJob)
                .where(PartnerPipelineJob.id == uuid.UUID(identifier))
                .with_for_update()
            )
            worker = pool.submit(partner_sync_tasks.process_pipeline, identifier, factory=database)
            claim.assert_waiting(worker)
        claim.resume()
        assert worker.result(timeout=5) is None
    with database() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        assert job.status == "succeeded" and job.attempts == 1


@pytest.mark.parametrize("kind", ["pipeline", "product", "evaluation"])
@pytest.mark.parametrize("failed", [False, True])
def test_result_and_failure_claims_release_catalog_while_delivery_rows_are_locked(
    admin_client, database, product_payload, monkeypatch, kind, failed
):
    from devfeed_aggregator.partner_tasks import process_evaluation

    entered, resume_io = Event(), Event()

    def external_io(*args):
        entered.set()
        assert resume_io.wait(timeout=3)
        if failed:
            raise FeedError("Transient fixture failure", retryable=True)
        if kind == "pipeline":
            return [], None
        if kind == "product":
            return ProductInput.model_validate(args[1].data)
        return answer(args[0])

    model = PartnerPipelineJob
    if kind == "pipeline":
        connect(admin_client)
        identifier = job_id(database, "sync")
        function, options = partner_sync_tasks.process_pipeline, {"reader": external_io}
    elif kind == "product":
        _parent, children = discover(admin_client, database, product_payload)
        identifier = children[0]
        function, options = partner_sync_tasks.process_pipeline, {"product_reader": external_io}
    else:
        article(database)
        qualified(admin_client, database, product_payload, monkeypatch)
        model = PartnerEvaluation
        with database() as session:
            identifier = str(session.scalar(select(PartnerEvaluation.id)))
        function, options = process_evaluation, {"assessor": external_io}
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        paused_claim_retry(monkeypatch, database) as claim,
    ):
        worker = pool.submit(function, identifier, factory=database, **options)
        assert entered.wait(timeout=2)
        with database.begin() as publisher:
            job = publisher.scalar(
                select(model).where(model.id == uuid.UUID(identifier)).with_for_update()
            )
            job.lease_until = utcnow() - timedelta(seconds=1)
            resume_io.set()
            claim.assert_waiting(worker)
        claim.resume()
        assert worker.result(timeout=5) is None
    with database() as session:
        job = session.get(model, uuid.UUID(identifier))
        assert job.status == ("queued" if failed else "succeeded")
        assert job.attempts == 1 and job.lease_token is None


def test_timed_out_rq_claim_remains_durable_and_recovers_without_losing_delivery(
    admin_client, database, monkeypatch
):
    connect(admin_client)
    identifier = uuid.UUID(job_id(database, "sync"))
    assert partner_sync_tasks.dispatch_partner_pipeline(database, schedule=False) == 1
    queue = get_queue("partner-pipeline")
    try:
        with database.begin() as publisher, monkeypatch.context() as patch:
            publisher.scalar(
                select(PartnerPipelineJob)
                .where(PartnerPipelineJob.id == identifier)
                .with_for_update()
            )
            clock = [0.0]
            patch.setattr(partner_claim, "CLAIM_TIMEOUT_SECONDS", 0.1)
            patch.setattr(
                partner_claim,
                "time",
                SimpleNamespace(
                    monotonic=lambda: clock[0],
                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
                ),
            )
            SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(
                burst=True
            )
        with database.begin() as session:
            job = session.get(PartnerPipelineJob, identifier)
            assert job.status == "queued" and job.attempts == 0
            job.dispatched_at = utcnow() - timedelta(seconds=61)
        assert partner_sync_tasks.dispatch_partner_pipeline(database, schedule=False) == 1
        monkeypatch.setattr(
            partner_sync_tasks.ConnectorClient, "discover_page", lambda *_args: ([], None)
        )
        SimpleWorker([queue], connection=queue.connection, serializer=JSONSerializer).work(
            burst=True
        )
        with database() as session:
            job = session.get(PartnerPipelineJob, identifier)
            assert job.status == "succeeded" and job.attempts == 1
    finally:
        queue.connection.close()

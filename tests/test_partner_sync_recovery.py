"""Durable partner imports fence stale inputs and recover without losing catalog state."""

import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_aggregator import partner_sync_tasks as pipeline
from devfeed_aggregator.codex_client import CodexClient
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import (
    PartnerConnection,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductURL,
    utcnow,
)
from devfeed_core.partner_catalog import refresh_availability, upsert_listing
from devfeed_core.partner_connections import request_sync
from devfeed_core.partner_tools import ProductInput
from devfeed_core.urls import fingerprint
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload

product_payload = shared_product_payload
pytestmark = pytest.mark.integration
PROVIDER = "nick-launches"


@pytest.fixture
def connection(database):
    with database.begin() as session:
        session.add(PartnerConnection(provider=PROVIDER, enabled=True))
    return PROVIDER


@pytest.fixture
def sync_job(database, connection):
    with database.begin() as session:
        job = request_sync(session, session.get(PartnerConnection, connection))
        session.flush()
        return job.id


@pytest.fixture
def assessment(database, connection, product_payload, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    item = ProductInput.model_validate(product_payload)
    with database.begin() as session:
        product = upsert_listing(session, item, uuid.uuid4())
        session.flush()
        identifier = session.scalar(
            select(PartnerPipelineJob.id).where(PartnerPipelineJob.operation == "assess")
        )
        return SimpleNamespace(job_id=identifier, product_id=product.id, item=item)


def unexpected_network(*args, **kwargs):
    pytest.fail("Stale or unowned work must not call external services")


def uncertain(*args):
    return {
        "decision": "uncertain",
        "reason": "The official page does not establish a specific supported capability.",
        "technologies": [],
        "evidence": [],
    }


def test_missing_job_is_a_safe_noop(database, caplog):
    with caplog.at_level(logging.DEBUG, logger=pipeline.__name__):
        pipeline.process_pipeline(str(uuid.uuid4()), factory=database, reader=unexpected_network)
    assert "partner_pipeline_not_found" in caplog.messages
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 0


def test_invalid_delivery_identifier_is_logged_and_propagated(database, caplog):
    with pytest.raises(ValueError):
        pipeline.process_pipeline("invalid-job-id", factory=database, reader=unexpected_network)

    failure = next(
        record for record in caplog.records if record.message == "partner_pipeline_runtime_failed"
    )
    assert failure.levelno == logging.ERROR and failure.error_type == "ValueError"
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 0


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_dispatch_limit_fails_before_scheduling_work(database, connection, limit):
    with pytest.raises(ValueError, match="Partner dispatch limit must be positive"):
        pipeline.dispatch_partner_pipeline(database, limit=limit)

    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 0


@pytest.mark.parametrize("checkpointed", [False, True], ids=["before-scan", "before-finalization"])
def test_changed_connection_fences_queued_generation(database, sync_job, checkpointed):
    with database.begin() as session:
        session.get(PartnerConnection, PROVIDER).sync_revision += 1
        if checkpointed:
            job = session.get(PartnerPipelineJob, sync_job)
            job.payload = {**job.payload, "discovery_complete": True, "pages": 1}

    pipeline.process_pipeline(str(sync_job), factory=database, reader=unexpected_network)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        assert job.status == "failed" and job.error == "Connection changed"
        assert job.attempts == 0 and job.finished_at is not None
        assert job.lease_token is None and job.lease_until is None
        assert session.get(PartnerConnection, PROVIDER).last_sync_at is None
        assert session.scalar(select(func.count()).select_from(PartnerListing)) == 0


@pytest.mark.parametrize("paused", ["disabled", "archived"])
def test_inactive_connection_does_not_start_queued_import(database, sync_job, paused):
    with database.begin() as session:
        connection = session.get(PartnerConnection, PROVIDER)
        if paused == "disabled":
            connection.enabled = False
        else:
            connection.archived_at = utcnow()

    pipeline.process_pipeline(str(sync_job), factory=database, reader=unexpected_network)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        assert job.status == "failed" and job.error == "Connection paused"
        assert job.attempts == 0 and job.lease_token is None


def test_expired_sync_at_retry_limit_records_terminal_connection_error(database, sync_job):
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        job.status, job.attempts = "running", 3
        job.lease_token, job.lease_until = uuid.uuid4(), utcnow() - timedelta(seconds=1)

    pipeline.process_pipeline(str(sync_job), factory=database, reader=unexpected_network)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        assert job.status == "failed" and job.attempts == 3
        assert job.error == "Automatic checks exhausted their retry limit"
        assert job.finished_at is not None and job.lease_token is None and job.lease_until is None
        assert session.get(PartnerConnection, PROVIDER).last_error == job.error


@pytest.mark.parametrize("status,retryable", [(401, False), (None, True)])
def test_upstream_failure_classification_preserves_existing_catalog(
    database, sync_job, assessment, status, retryable
):
    def read(cursor):
        raise FeedError("private upstream body and credentials", status=status, retryable=retryable)

    pipeline.process_pipeline(str(sync_job), factory=database, reader=read)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        assert job.status == ("queued" if retryable else "failed") and job.attempts == 1
        assert job.error == "Platform API could not be read" + (" (HTTP 401)" if status else "")
        assert job.lease_token is None and job.lease_until is None
        if retryable:
            assert job.available_at > utcnow() and job.finished_at is None
        else:
            assert job.finished_at is not None
        connection = session.get(PartnerConnection, PROVIDER)
        assert connection.last_error == job.error and connection.last_sync_at is None
        assert session.scalar(select(PartnerListing)).active
        assert session.get(PartnerProduct, assessment.product_id).status != "withdrawn"


@pytest.mark.parametrize("change", ["revision", "excluded", "source-removed"])
def test_changed_product_is_rechecked_before_fetch_and_only_current_revision_is_requeued(
    database, assessment, change
):
    with database.begin() as session:
        product = session.get(PartnerProduct, assessment.product_id)
        if change == "revision":
            product.revision += 1
        elif change == "excluded":
            product.excluded = True
        else:
            session.scalar(select(PartnerListing)).active = False
            refresh_availability(session, product)
            assert product.status == "withdrawn"

    pipeline.process_pipeline(
        str(assessment.job_id),
        factory=database,
        page_fetcher=unexpected_network,
        assessor=unexpected_network,
    )

    with database() as session:
        job = session.get(PartnerPipelineJob, assessment.job_id)
        assert job.status == "failed"
        assert job.error == (
            "Product source is unavailable" if change == "source-removed" else "Product changed"
        )
        assert job.attempts == 0
        pending = session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status == "queued")
        ).all()
        if change == "revision":
            assert len(pending) == 1 and pending[0].id != assessment.job_id
            assert pending[0].product_id == assessment.product_id
            assert pending[0].payload["revision"] == 2
        else:
            assert pending == []


@pytest.mark.parametrize("change", ["lease-replaced", "cancelled"])
def test_website_fetch_cannot_continue_after_lease_loss(database, assessment, change, caplog):
    replacement = uuid.uuid4()

    def fetch(url, timeout):
        with database.begin() as session:
            job = session.get(PartnerPipelineJob, assessment.job_id)
            assert job.status == "running" and job.lease_token is not None
            if change == "lease-replaced":
                job.lease_token = replacement
            else:
                job.status, job.error = "failed", "Cancelled by operator"
                job.lease_token = job.lease_until = None
        return {"final_url": url, "text": "Developer product details."}

    pipeline.process_pipeline(
        str(assessment.job_id), factory=database, page_fetcher=fetch, assessor=unexpected_network
    )

    with database() as session:
        job = session.get(PartnerPipelineJob, assessment.job_id)
        if change == "lease-replaced":
            assert job.status == "running" and job.lease_token == replacement
        else:
            assert job.status == "failed" and job.error == "Cancelled by operator"
        product = session.get(PartnerProduct, assessment.product_id)
        assert product.status == "pending" and product.assessment_revision == 0
        assert product.evidence == [] and product.verified_at is None
    assert "partner_pipeline_result_discarded" in caplog.messages


@pytest.mark.parametrize("change", ["revision", "source-paused"])
def test_website_fetch_rechecks_product_input_before_paying_for_inference(
    database, assessment, change
):
    def fetch(url, timeout):
        with database.begin() as session:
            if change == "revision":
                session.get(PartnerProduct, assessment.product_id).revision += 1
            else:
                session.get(PartnerConnection, PROVIDER).enabled = False
        return {"final_url": url, "text": "Developer product details."}

    pipeline.process_pipeline(
        str(assessment.job_id), factory=database, page_fetcher=fetch, assessor=unexpected_network
    )

    with database() as session:
        job = session.get(PartnerPipelineJob, assessment.job_id)
        assert job.status == "failed" and job.error == "Product or connection changed"
        assert job.lease_token is None and job.lease_until is None
        pending = session.scalars(
            select(PartnerPipelineJob).where(PartnerPipelineJob.status == "queued")
        ).all()
        if change == "revision":
            assert len(pending) == 1 and pending[0].payload["revision"] == 2
            assert pending[0].product_id == assessment.product_id
        else:
            assert pending == []


def test_finalization_withdraws_in_bounded_batches_before_marking_sync_complete(
    database, sync_job, assessment
):
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        job.payload = {**job.payload, "discovery_complete": True, "pages": 1}
        for index in range(25):
            session.add(
                PartnerListing(
                    provider=PROVIDER,
                    product_id=assessment.product_id,
                    external_id=f"stale-{index}",
                    name="Old product listing",
                    product_url=assessment.item.product_url,
                    listing_url=f"https://nicklaunches.com/stale-{index}",
                    tagline="An older listing of the same developer product.",
                )
            )

    pipeline.process_pipeline(str(sync_job), factory=database, reader=unexpected_network)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        assert job.status == "queued" and job.attempts == 0
        assert job.payload["withdrawn"] == 25 and job.payload["finalize_batches"] == 1
        assert job.lease_token is None and job.dispatched_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerListing)
                .where(PartnerListing.active.is_(True))
            )
            == 1
        )
        assert session.get(PartnerProduct, assessment.product_id).status != "withdrawn"
        assert session.get(PartnerConnection, PROVIDER).last_sync_at is None

    pipeline.process_pipeline(str(sync_job), factory=database, reader=unexpected_network)

    with database() as session:
        job = session.get(PartnerPipelineJob, sync_job)
        connection = session.get(PartnerConnection, PROVIDER)
        assert job.status == "succeeded" and job.finished_at is not None
        assert job.payload["withdrawn"] == 26 and job.payload["finalize_batches"] == 2
        assert session.get(PartnerProduct, assessment.product_id).status == "withdrawn"
        assert connection.last_sync_at is not None and connection.last_error is None
        assert connection.next_sync_at > connection.last_sync_at


@pytest.mark.parametrize("known_alias", [False, True], ids=["fetch-failed", "already-verified"])
def test_redirect_proof_failure_is_safe_and_verified_aliases_need_no_network(
    database, assessment, known_alias, caplog
):
    changed = assessment.item.model_copy(update={"product_url": "https://www.checker.example/"})
    if known_alias:
        with database.begin() as session:
            session.add(
                PartnerProductURL(
                    url_hash=fingerprint(changed.product_url),
                    url=changed.product_url,
                    product_id=assessment.product_id,
                    verified_at=utcnow(),
                )
            )
    fetched = []

    def fetch(url, timeout):
        fetched.append(url)
        raise FeedError("private upstream details", status=503, retryable=True)

    with caplog.at_level(logging.INFO, logger=pipeline.__name__):
        assert pipeline.pending_redirect_proofs(database, [changed], fetch) == []

    assert fetched == ([] if known_alias else [assessment.item.product_url])
    if not known_alias:
        assert "partner_listing_identity_unresolved" in caplog.messages
    assert "private upstream details" not in caplog.text
    with database() as session:
        listing = session.scalar(select(PartnerListing))
        assert listing.active and listing.product_id == assessment.product_id


def test_default_qualification_uses_bounded_official_evidence_and_durable_result(
    database, assessment, monkeypatch
):
    text = "Official developer product evidence. " + "x" * 20_000 + "excluded-tail"
    calls = []

    def complete(client, prompt, schema):
        calls.append(client.operation)
        assert assessment.item.name in prompt
        assert "Official developer product evidence." in prompt
        assert "excluded-tail" not in prompt
        assert set(schema["properties"]["decision"]["enum"]) == {
            "qualified",
            "irrelevant",
            "uncertain",
        }
        return uncertain()

    monkeypatch.setattr(CodexClient, "complete", complete)
    pipeline.process_pipeline(
        str(assessment.job_id),
        factory=database,
        page_fetcher=lambda url, timeout: {"final_url": url, "text": text},
    )

    assert calls == ["partner_qualification"]
    with database() as session:
        job = session.get(PartnerPipelineJob, assessment.job_id)
        product = session.get(PartnerProduct, assessment.product_id)
        assert job.status == "succeeded" and job.error is None and job.finished_at is not None
        assert job.lease_token is None and job.lease_until is None
        assert product.status == "pending" and product.assessment["state"] == "attention"
        assert product.assessment_revision == product.revision
        assert product.technologies == product.evidence == []

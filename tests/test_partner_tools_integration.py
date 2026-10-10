"""API-only launch platform lifecycle with durable sync and independent qualification."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline, schedule_partner_syncs
from devfeed_aggregator.partner_tasks import process_evaluation
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_tools import ProductInput
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload

product_payload = shared_product_payload
pytestmark = pytest.mark.integration
ROOT = "/v1/admin/partner-tools"
CONNECTION = ROOT + "/connections/nick-launches"


def connect(client):
    response = client.post(CONNECTION, json={"action": "connect"})
    assert response.status_code == 200, response.text
    assert response.json()["partnership_type"] == "launch_platform"


def job_id(database, operation):
    with database() as session:
        return str(
            session.scalar(
                select(PartnerPipelineJob.id).where(
                    PartnerPipelineJob.operation == operation, PartnerPipelineJob.status == "queued"
                )
            )
        )


def finish_sync(database, identifier, **kwargs):
    """Run individual product workers and the completion checkpoint explicitly."""
    with database() as session:
        children = session.scalars(
            select(PartnerPipelineJob.id).where(
                PartnerPipelineJob.parent_id == uuid.UUID(identifier),
                PartnerPipelineJob.status == "queued",
            )
        ).all()
    for child in children:
        process_pipeline(str(child), factory=database, **kwargs)
    with database.begin() as session:
        parent = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        parent.available_at = utcnow() - timedelta(seconds=1)
    process_pipeline(identifier, factory=database)


def synced(client, database, payload):
    connect(client)
    item = ProductInput.model_validate({**payload, "technologies": [], "evidence": []})
    identifier = job_id(database, "sync")
    process_pipeline(identifier, factory=database, reader=lambda _: ([item], None))
    finish_sync(database, identifier)
    return client.get(ROOT).json()["items"][0]


def qualified(client, database, payload, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = synced(client, database, payload)
    page = {"final_url": product["product_url"], "text": payload["evidence"][0]["quote"]}
    process_pipeline(
        job_id(database, "assess"),
        factory=database,
        page_fetcher=lambda *_: page,
        assessor=lambda *_: {
            "decision": "qualified",
            "reason": "Supports specific OpenAPI compatibility checks.",
            "technologies": ["OpenAPI"],
            "evidence": [{**payload["evidence"][0], "url": page["final_url"]}],
        },
    )
    return client.get(ROOT).json()["items"][0]


def article(database, kind="tutorial"):
    with database.begin() as session:
        value = Article(
            canonical_url="https://publisher.example/" + uuid.uuid4().hex,
            url_hash=uuid.uuid4().hex,
            title="OpenAPI compatibility in CI",
            summary="Detect breaking OpenAPI changes before merging a pull request.",
            slug=uuid.uuid4().hex,
            publication_status="published",
            review_status="approved",
            content_type=kind,
        )
        session.add(value)
        session.flush()
        return str(value.id)


def answer(snapshot):
    return {
        "decisions": [
            {
                "article_id": a["id"],
                "relevant": a["content_type"] == "tutorial",
                "reason": "Checks the specific API compatibility task described in the tutorial.",
                "article_quote": a["text"] if a["content_type"] == "tutorial" else "",
                "evidence_index": 0 if a["content_type"] == "tutorial" else -1,
                "technology": "OpenAPI" if a["content_type"] == "tutorial" else "",
            }
            for a in snapshot["articles"]
        ]
    }


def test_api_only_sync_is_idempotent_and_never_publishes(admin_client, database, product_payload):
    product = synced(admin_client, database, product_payload)
    assert product["assessment"]["state"] == "checking"
    assert product["evidence"] == [] and not product["eligible"]
    assert synced(admin_client, database, product_payload)["revision"] == product["revision"]
    for path in [
        "/import",
        "/import/nick",
        f"/{product['id']}/review",
        f"/{product['id']}/evaluations",
    ]:
        assert admin_client.post(ROOT + path, json={"products": [product_payload]}).status_code in {
            404,
            405,
        }
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        assert session.scalar(select(func.count()).select_from(PartnerProduct)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPipelineJob)
                .where(PartnerPipelineJob.operation == "assess")
            )
            == 1
        )


def test_automatic_qualification_and_matching(admin_client, database, product_payload, monkeypatch):
    article(database)
    article(database, "news")
    product = qualified(admin_client, database, product_payload, monkeypatch)
    assert product["eligible"] and product["assessment"]["state"] == "qualified"
    with database() as session:
        evaluation = session.scalar(select(PartnerEvaluation))
        identifier = str(evaluation.id)
    process_evaluation(identifier, factory=database, assessor=answer)
    process_evaluation(identifier, factory=database, assessor=lambda _: pytest.fail("Duplicate"))
    result = admin_client.get(f"{ROOT}/{product['id']}/evaluations").json()[0]
    assert result["current"] and result["status"] == "succeeded"
    assert [d["relevant"] for d in result["result"]["decisions"]] == [True, False]
    path = f"{ROOT}/{product['id']}/actions"
    excluded = admin_client.post(
        path, json={"action": "exclude", "expected_revision": product["revision"]}
    )
    assert excluded.status_code == 200
    assert excluded.json()["excluded"] and not excluded.json()["eligible"]
    assert not admin_client.get(f"{ROOT}/{product['id']}/evaluations").json()[0]["current"]
    assert (
        admin_client.post(
            path, json={"action": "retry", "expected_revision": product["revision"]}
        ).status_code
        == 409
    )
    retried = admin_client.post(
        path, json={"action": "retry", "expected_revision": excluded.json()["revision"]}
    )
    assert retried.status_code == 200 and retried.json()["assessment"]["state"] == "checking"


def test_connection_pause_discards_inflight_sync(admin_client, database, product_payload):
    connect(admin_client)

    def reader(_):
        assert admin_client.post(CONNECTION, json={"action": "pause"}).status_code == 200
        return [ProductInput.model_validate(product_payload)], None

    process_pipeline(job_id(database, "sync"), factory=database, reader=reader)
    assert admin_client.get(ROOT).json()["total"] == 0
    connect(admin_client)
    assert job_id(database, "sync") != "None"


def test_failed_partial_scan_keeps_catalog_and_completed_scan_withdraws(
    admin_client, database, product_payload
):
    product = synced(admin_client, database, product_payload)
    admin_client.post(CONNECTION, json={"action": "sync"})
    identifier = job_id(database, "sync")
    process_pipeline(identifier, factory=database, reader=lambda _: ([], "next"))
    with database.begin() as session:
        session.get(PartnerPipelineJob, uuid.UUID(identifier)).available_at = utcnow() - timedelta(
            seconds=1
        )
    process_pipeline(
        identifier, factory=database, reader=lambda _: (_ for _ in ()).throw(ValueError("bad data"))
    )
    assert admin_client.get(ROOT).json()["items"][0]["status"] != "withdrawn"
    with database.begin() as session:
        session.get(PartnerPipelineJob, uuid.UUID(identifier)).available_at = utcnow() - timedelta(
            seconds=1
        )
    process_pipeline(identifier, factory=database, reader=lambda _: ([], None))
    assert admin_client.get(ROOT).json()["items"][0]["status"] == "withdrawn"
    assert (
        admin_client.post(
            f"{ROOT}/{product['id']}/actions",
            json={"action": "retry", "expected_revision": product["revision"] + 1},
        ).status_code
        == 409
    )


def test_product_change_during_qualification_is_rechecked(
    admin_client, database, product_payload, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = synced(admin_client, database, product_payload)
    identifier = job_id(database, "assess")

    def assess(*_):
        with database.begin() as session:
            session.get(PartnerProduct, uuid.UUID(product["id"])).revision += 1
        return {
            "decision": "uncertain",
            "reason": "No reliable evidence found.",
            "technologies": [],
            "evidence": [],
        }

    process_pipeline(
        identifier,
        factory=database,
        page_fetcher=lambda *_: {"final_url": product["product_url"], "text": "Text"},
        assessor=assess,
    )
    schedule_partner_syncs(database)
    assert job_id(database, "assess") not in {"None", identifier}
    assert not admin_client.get(ROOT).json()["items"][0]["eligible"]


def test_daily_schedule_coalesces_syncs(admin_client, database):
    connect(admin_client)
    with database.begin() as session:
        session.get(PartnerConnection, "nick-launches").next_sync_at = utcnow() - timedelta(hours=1)
    schedule_partner_syncs(database)
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPipelineJob)
                .where(PartnerPipelineJob.operation == "sync")
            )
            == 1
        )


def test_api_rate_limits_retry_and_ai_disabled_still_syncs(admin_client, database, monkeypatch):
    from devfeed_core.feeds.fetcher import FeedError

    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    connect(admin_client)
    identifier = job_id(database, "sync")

    def throttled(_):
        raise FeedError(
            "Do not expose remote response text", status=429, retryable=True, retry_after=600
        )

    process_pipeline(identifier, factory=database, reader=throttled)
    with database() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        assert job.status == "queued" and job.attempts == 1
        assert job.available_at >= utcnow() + timedelta(seconds=590)
        assert "429" in job.error and "remote response" not in job.error
    response = admin_client.get(ROOT + "/connections").json()[0]
    assert "429" in response["error"] and not response["ai_enabled"]


def test_exclusion_survives_source_update(admin_client, database, product_payload):
    product = synced(admin_client, database, product_payload)
    response = admin_client.post(
        f"{ROOT}/{product['id']}/actions",
        json={"action": "exclude", "expected_revision": product["revision"]},
    )
    assert response.status_code == 200
    product_payload["description"] += " Additional capabilities."
    updated = synced(admin_client, database, product_payload)
    assert updated["excluded"] and not updated["eligible"]
    assert job_id(database, "assess") == "None"


def test_repeated_worker_expiry_stops_automatic_retries(
    admin_client, database, product_payload, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    synced(admin_client, database, product_payload)
    identifier = job_id(database, "assess")
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, uuid.UUID(identifier))
        job.attempts, job.status, job.lease_until = 3, "running", utcnow() - timedelta(seconds=1)
    process_pipeline(identifier, factory=database, assessor=lambda *_: pytest.fail("Retry limit"))
    schedule_partner_syncs(database)
    assert job_id(database, "assess") == "None"
    assert admin_client.get(ROOT).json()["items"][0]["assessment"]["state"] == "attention"


def test_changed_synced_product_automatically_requalifies(
    admin_client, database, product_payload, monkeypatch
):
    product = qualified(admin_client, database, product_payload, monkeypatch)
    product_payload["description"] += " Updated official features."
    updated = synced(admin_client, database, product_payload)
    assert updated["revision"] == product["revision"] + 1
    assert updated["assessment"]["state"] == "checking" and not updated["eligible"]
    assert updated["evidence"] == [] and updated["verified_at"] is None


def test_dispatch_uses_separate_network_and_ai_queues(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_aggregator.partner_sync_tasks import dispatch_partner_pipeline
    from devfeed_aggregator.queue import get_queue

    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    connect(admin_client)
    assert dispatch_partner_pipeline(database) == 1
    assert dispatch_partner_pipeline(database) == 0
    network = get_queue("ingestion")
    analysis = get_queue("source-analysis")
    try:
        assert len(network.jobs) == 1 and not analysis.jobs
        assert network.jobs[0].func_name == "devfeed_aggregator.partner_sync_tasks.process_pipeline"
        synced(admin_client, database, product_payload)
        assert dispatch_partner_pipeline(database) == 0
        monkeypatch.setattr(get_settings(), "ai_enabled", True)
        assert dispatch_partner_pipeline(database) == 1
        assert len(analysis.jobs) == 1
    finally:
        network.connection.close()
        analysis.connection.close()


def test_pause_resume_recovers_expired_qualification(
    admin_client, database, product_payload, monkeypatch
):
    product = qualified(admin_client, database, product_payload, monkeypatch)
    with database.begin() as session:
        session.get(PartnerProduct, uuid.UUID(product["id"])).verified_at = utcnow() - timedelta(
            days=91
        )
    renewed = synced(admin_client, database, product_payload)
    assert renewed["assessment"]["state"] == "checking"
    assert admin_client.post(CONNECTION, json={"action": "pause"}).status_code == 200
    connect(admin_client)
    schedule_partner_syncs(database)
    assert job_id(database, "assess") != "None"


def test_connection_actions_log_committed_changes(admin_client, database, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="devfeed_admin_api.partner_tools"):
        connect(admin_client)
        identifier = job_id(database, "sync")
        assert admin_client.post(CONNECTION, json={"action": "sync"}).status_code == 200
        assert admin_client.post(CONNECTION, json={"action": "pause"}).status_code == 200
    events = {
        record.message: record for record in caplog.records if record.message.startswith("partner_")
    }
    assert events["partner_connection_connected"].provider == "nick-launches"
    assert events["partner_sync_requested"].job_ids == [identifier]
    paused = events["partner_connection_paused"]
    assert paused.jobs_cancelled == 1 and paused.job_ids == [identifier]
    assert not paused.enabled
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(identifier)).status == "failed"


def test_worker_progress_is_available_in_job_logs(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_core.job_logs import capture_runtime_logs

    article(database)
    with capture_runtime_logs():
        product = qualified(admin_client, database, product_payload, monkeypatch)
        with database() as session:
            pipeline = [
                (str(j.id), j.operation) for j in session.scalars(select(PartnerPipelineJob)).all()
            ]
            evaluation_id = str(session.scalar(select(PartnerEvaluation.id)))
        process_evaluation(evaluation_id, factory=database, assessor=answer)
    for identifier, operation in pipeline:
        response = admin_client.get(f"/v1/admin/jobs/partner-pipeline/{identifier}/logs")
        assert response.status_code == 200, response.text
        events = {item["fields"]["event"]: item for item in response.json()["items"]}
        assert events["partner_pipeline_started"]["fields"]["provider"] == "nick-launches"
        assert events["partner_pipeline_started"]["fields"]["attempt"] == 1
        if operation == "sync":
            assert events["partner_sync_page_processed"]["fields"]["products_received"] == 1
            assert events["partner_sync_completed"]["fields"]["pages_processed"] == 1
        elif operation == "sync_product":
            assert events["partner_product_sync_completed"]["fields"]["product_id"] == product["id"]
        else:
            assert events["partner_assessment_completed"]["fields"]["decision"] == "qualified"
            assert events["partner_assessment_completed"]["fields"]["product_id"] == product["id"]
    response = admin_client.get(f"/v1/admin/jobs/partner-evaluation/{evaluation_id}/logs")
    assert response.status_code == 200, response.text
    completed = next(
        item
        for item in response.json()["items"]
        if item["fields"]["event"] == "partner_evaluation_completed"
    )
    assert completed["fields"]["matches"] == 1


def test_worker_retry_logs_include_http_status_and_backoff(admin_client, database):
    from devfeed_core.feeds.fetcher import FeedError
    from devfeed_core.job_logs import capture_runtime_logs

    connect(admin_client)
    identifier = job_id(database, "sync")

    def throttled(_):
        raise FeedError("untrusted API response", status=429, retryable=True, retry_after=600)

    with capture_runtime_logs():
        process_pipeline(identifier, factory=database, reader=throttled)
    response = admin_client.get(f"/v1/admin/jobs/partner-pipeline/{identifier}/logs")
    assert response.status_code == 200, response.text
    retry = next(
        item
        for item in response.json()["items"]
        if item["fields"]["event"] == "partner_pipeline_retry_scheduled"
    )
    assert retry["fields"]["http_status"] == 429
    assert retry["fields"]["retry_at"] and retry["fields"]["status"] == "queued"
    assert "untrusted API response" not in response.text


@pytest.mark.parametrize("running", [False, True])
def test_recheck_replaces_active_evaluation_without_another_sync(
    admin_client, database, product_payload, monkeypatch, running
):
    article(database)
    product = qualified(admin_client, database, product_payload, monkeypatch)
    with database() as session:
        previous = str(session.scalar(select(PartnerEvaluation.id)))

    def recheck():
        response = admin_client.post(
            f"{ROOT}/{product['id']}/actions",
            json={"action": "retry", "expected_revision": product["revision"]},
        )
        assert response.status_code == 200
        page = {
            "final_url": product["product_url"],
            "text": product_payload["evidence"][0]["quote"],
        }
        process_pipeline(
            job_id(database, "assess"),
            factory=database,
            page_fetcher=lambda *_: page,
            assessor=lambda *_: {
                "decision": "qualified",
                "reason": "Supports specific OpenAPI compatibility checks.",
                "technologies": ["OpenAPI"],
                "evidence": [{**product_payload["evidence"][0], "url": page["final_url"]}],
            },
        )

    if running:

        def in_flight(snapshot):
            recheck()  # Requalification finishes before the old evaluation returns.
            return answer(snapshot)

        process_evaluation(previous, factory=database, assessor=in_flight)
    else:
        recheck()
        process_evaluation(
            previous, factory=database, assessor=lambda _: pytest.fail("Stale delivery")
        )
    with database() as session:
        old = session.get(PartnerEvaluation, uuid.UUID(previous))
        assert old.status == "failed" and old.lease_token is None
        assert not old.result
        replacement = session.scalar(
            select(PartnerEvaluation).where(PartnerEvaluation.status == "queued")
        )
        assert replacement is not None and str(replacement.id) != previous
        assert replacement.snapshot["product"]["revision"] == product["revision"] + 1
        identifier = str(replacement.id)
    process_evaluation(identifier, factory=database, assessor=answer)
    results = admin_client.get(f"{ROOT}/{product['id']}/evaluations").json()
    assert results[0]["id"] == identifier and results[0]["current"]
    assert results[0]["status"] == "succeeded"


def test_product_status_filter_counts_and_pages_only_approved(admin_client, database):
    with database() as session:
        session.add(PartnerConnection(provider="nick-launches", enabled=False))
        session.flush()
        for name, status in [
            ("A rejected", "rejected"),
            ("B approved", "approved"),
            ("C pending", "pending"),
            ("D approved", "approved"),
        ]:
            product = PartnerProduct(
                name=name,
                product_url="https://example.test/" + name[0],
                description="Fixture product",
                status=status,
            )
            session.add(product)
            session.flush()
            session.add(
                PartnerListing(
                    product_id=product.id,
                    provider="nick-launches",
                    external_id=name[0],
                    name=name,
                    product_url=product.product_url,
                    listing_url="https://listing.test/" + name[0],
                    description="Fixture listing",
                )
            )
        session.commit()
    page = admin_client.get(ROOT, params={"status": "approved", "sort": "name", "limit": 1}).json()
    assert page["total"] == 2
    assert [item["name"] for item in page["items"]] == ["B approved"]
    second = admin_client.get(
        ROOT, params={"status": "approved", "sort": "name", "limit": 1, "offset": 1}
    ).json()
    assert second["total"] == 2
    assert [item["name"] for item in second["items"]] == ["D approved"]
    assert admin_client.get(ROOT).json()["total"] == 4
    assert admin_client.get(ROOT + "?status=invalid").status_code == 422

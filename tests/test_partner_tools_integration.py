"""Real PostgreSQL outbox, admin review, and private evaluation lifecycle."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.partner_tasks import process_evaluation
from devfeed_core.config import get_settings
from devfeed_core.models import Article, PartnerEvaluation, PartnerProduct, utcnow
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload

product_payload = shared_product_payload

pytestmark = pytest.mark.integration
ROOT = "/v1/admin/partner-tools"


def imported(client, payload):
    response = client.post(ROOT + "/import", json={"products": [payload]})
    assert response.status_code == 200, response.text
    return response.json()[0]


def approve(client, identifier):
    revision = next(
        p["revision"] for p in client.get(ROOT).json()["items"] if p["id"] == identifier
    )
    response = client.post(
        f"{ROOT}/{identifier}/review",
        json={
            "status": "approved",
            "expected_revision": revision,
            "note": "Reviewed official documentation and permission.",
            "evidence_checked": True,
            "display_rights_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def article(database, kind="tutorial"):
    with database.begin() as session:
        value = Article(
            canonical_url="https://publisher.example/tutorial",
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


def test_import_review_update_and_no_publication(admin_client, database, product_payload):
    product = imported(admin_client, product_payload)
    assert product["status"] == "pending" and not product["eligible"]
    assert imported(admin_client, product_payload)["id"] == product["id"]
    path = f"{ROOT}/{product['id']}"
    assert (
        admin_client.post(
            path + "/review", json={"status": "approved", "note": "Unchecked evidence"}
        ).status_code
        == 422
    )
    approved = approve(admin_client, product["id"])
    assert approved["eligible"]
    assert imported(admin_client, product_payload)["revision"] == approved["revision"]
    product_payload["description"] += " Changed capabilities."
    updated = imported(admin_client, product_payload)
    assert updated["status"] == "pending" and updated["verified_at"] is None
    assert updated["revision"] == approved["revision"] + 1
    page = admin_client.get(ROOT).json()
    assert page["total"] == 1 and len(page["items"]) == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 0
        assert session.scalar(select(func.count()).select_from(PartnerProduct)) == 1


def test_evaluation_results_reviews_and_staleness(
    admin_client, database, product_payload, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = imported(admin_client, product_payload)
    path = f"{ROOT}/{product['id']}"
    assert admin_client.post(path + "/evaluations", json={}).status_code == 409
    approve(admin_client, product["id"])
    identifier = article(database)
    article(database, "news")
    response = admin_client.post(path + "/evaluations", json={})
    assert response.status_code == 202, response.text
    run = response.json()
    assert len(run["snapshot"]["articles"]) == 2
    assert admin_client.post(path + "/evaluations", json={}).status_code == 409
    process_evaluation(run["id"], factory=database, assessor=answer)
    process_evaluation(
        run["id"], factory=database, assessor=lambda _: pytest.fail("Duplicate execution")
    )
    result = admin_client.get(path + "/evaluations").json()[0]
    assert result["status"] == "succeeded" and result["current"]
    assert {d["relevant"] for d in result["result"]["decisions"]} == {True, False}
    review = {
        "article_id": identifier,
        "decision": "accepted",
        "note": "Specific task and technology match.",
    }
    review_path = path + f"/evaluations/{run['id']}/review"
    assert admin_client.post(review_path, json=review).status_code == 200
    with database.begin() as session:
        session.get(
            Article, uuid.UUID(identifier)
        ).summary = "An edited article with different evidence."
    assert admin_client.get(path + "/evaluations").json()[0]["current"] is False
    assert admin_client.post(review_path, json=review).status_code == 409


def test_changes_during_inference_cannot_write_current_results(
    admin_client, database, product_payload, monkeypatch
):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = imported(admin_client, product_payload)
    approve(admin_client, product["id"])
    article(database)
    path = f"{ROOT}/{product['id']}"
    run = admin_client.post(path + "/evaluations", json={}).json()

    def paused(snapshot):
        response = admin_client.post(
            path + "/review",
            json={"status": "paused", "expected_revision": 2, "note": "Pause during evaluation."},
        )
        assert response.status_code == 200
        return answer(snapshot)

    process_evaluation(run["id"], factory=database, assessor=paused)
    result = admin_client.get(path + "/evaluations").json()[0]
    assert result["status"] == "failed" and result["result"] is None
    assert not result["current"]


def test_recovery_and_bounded_retry(admin_client, database, product_payload, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = imported(admin_client, product_payload)
    approve(admin_client, product["id"])
    article(database)
    path = f"{ROOT}/{product['id']}"
    run = admin_client.post(path + "/evaluations", json={}).json()
    for attempt in range(3):
        process_evaluation(run["id"], factory=database, assessor=lambda _: {"decisions": []})
        with database.begin() as session:
            job = session.get(PartnerEvaluation, uuid.UUID(run["id"]))
            assert job.attempts == attempt + 1
            job.available_at = utcnow() - timedelta(seconds=1)
    result = admin_client.get(path + "/evaluations").json()[0]
    assert result["status"] == "failed"
    assert result["error"] == "Evaluation failed or returned unsupported evidence"


def test_nick_import_preserves_reviewed_evidence(admin_client, database, product_payload):
    product = imported(admin_client, product_payload)
    approved = approve(admin_client, product["id"])
    native = {
        "slug": product_payload["external_id"],
        "name": product_payload["name"],
        "url": product_payload["listing_url"],
        "productUrl": product_payload["product_url"],
        "description": product_payload["description"],
        "pricing": "free",
        "upvotes": 123,
    }
    response = admin_client.post(ROOT + "/import/nick", json={"products": [native]})
    assert response.status_code == 200, response.text
    value = response.json()[0]
    assert value["evidence"] == product_payload["evidence"]
    assert value["technologies"] == ["OpenAPI"]
    # Attribution supplied by the adapter is a material edit on the first native import.
    approve(admin_client, product["id"])
    stable = admin_client.post(ROOT + "/import/nick", json={"products": [native]}).json()[0]
    assert stable["eligible"]
    native["description"] += " Vendor description changed."
    changed = admin_client.post(ROOT + "/import/nick", json={"products": [native]}).json()[0]
    assert changed["status"] == "pending" and not changed["eligible"]
    assert changed["revision"] > approved["revision"]


def test_stale_approval_is_rejected(admin_client, database, product_payload):
    product = imported(admin_client, product_payload)
    product_payload["description"] += " An important new claim."
    imported(admin_client, product_payload)
    response = admin_client.post(
        f"{ROOT}/{product['id']}/review",
        json={
            "expected_revision": product["revision"],
            "status": "approved",
            "note": "Reviewed an old version of the product.",
            "evidence_checked": True,
            "display_rights_confirmed": True,
        },
    )
    assert response.status_code == 409


def test_dispatch_and_recover_expired_lease(admin_client, database, product_payload, monkeypatch):
    from devfeed_aggregator.partner_tasks import dispatch_partner_evaluations
    from devfeed_aggregator.queue import get_queue

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = imported(admin_client, product_payload)
    approve(admin_client, product["id"])
    article(database)
    path = f"{ROOT}/{product['id']}"
    run = admin_client.post(path + "/evaluations", json={}).json()
    assert dispatch_partner_evaluations(database) == 1
    assert dispatch_partner_evaluations(database) == 0
    queue = get_queue("source-analysis")
    try:
        delivery = queue.fetch_job(queue.job_ids[0])
        assert delivery.func_name == "devfeed_aggregator.partner_tasks.process_evaluation"
        assert list(delivery.args) == [run["id"]]
        queue.empty()
    finally:
        queue.connection.close()
    with database.begin() as session:
        job = session.get(PartnerEvaluation, uuid.UUID(run["id"]))
        job.status, job.attempts = "running", 1
        job.lease_token = uuid.uuid4()
        job.lease_until = utcnow() - timedelta(seconds=1)
    process_evaluation(run["id"], factory=database, assessor=answer)
    result = admin_client.get(path + "/evaluations").json()[0]
    assert result["status"] == "succeeded"

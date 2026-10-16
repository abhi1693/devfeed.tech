"""Browsable partner job APIs: filtered lists, stable detail URLs, and evidence."""

import uuid

import pytest
from devfeed_aggregator.partner_tasks import process_evaluation
from devfeed_core.models import PartnerEvaluation
from sqlalchemy import select
from test_partner_tools import product_payload as shared_product_payload
from test_partner_tools_integration import ROOT, answer, article, qualified, synced

product_payload = shared_product_payload
pytestmark = pytest.mark.integration


def test_pipeline_list_filters_pagination_and_detail(admin_client, database, product_payload):
    product = synced(admin_client, database, product_payload)
    response = admin_client.get(ROOT + "/pipeline", params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3 and len(response.json()["items"]) == 1
    page = admin_client.get(
        ROOT + "/pipeline",
        params={
            "operation": "sync_product",
            "status": "succeeded",
            "provider": "nick-launches",
            "q": product["name"],
        },
    ).json()
    assert page["total"] == 1
    child = page["items"][0]
    assert child["product_name"] == product["name"] and child["external_id"]
    assert "payload" not in child and "lease_token" not in child
    detail = admin_client.get(ROOT + "/pipeline/" + child["id"])
    assert detail.status_code == 200 and detail.json() == child
    related = admin_client.get(ROOT + "/pipeline", params={"parent_id": child["parent_id"]}).json()
    assert [j["id"] for j in related["items"]] == [child["id"]]
    assert (
        admin_client.get(ROOT + "/pipeline", params={"product_id": product["id"]}).json()["total"]
        == 2
    )
    assert admin_client.get(ROOT + "/pipeline", params={"provider": "missing"}).json()["total"] == 0
    assert admin_client.get(ROOT + "/pipeline", params={"status": "bad"}).status_code == 422
    assert admin_client.get(ROOT + "/pipeline", params={"sort": "payload"}).status_code == 422
    assert admin_client.get(ROOT + "/pipeline/" + str(uuid.uuid4())).status_code == 404


def test_evaluation_history_list_and_detail_keep_results(
    admin_client, database, product_payload, monkeypatch
):
    article(database)
    product = qualified(admin_client, database, product_payload, monkeypatch)
    with database() as session:
        identifier = str(session.scalar(select(PartnerEvaluation.id)))
    process_evaluation(identifier, factory=database, assessor=answer)
    response = admin_client.get(
        ROOT + "/evaluations",
        params={
            "status": "succeeded",
            "product_id": product["id"],
            "provider": "nick-launches",
            "q": product["name"],
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json()["items"][0]
    assert summary["id"] == identifier and summary["matches"] == 1 and summary["articles"] == 1
    assert summary["attempts"] == 1
    assert "snapshot" not in summary and "result" not in summary
    detail = admin_client.get(ROOT + "/evaluations/" + identifier)
    assert detail.status_code == 200, detail.text
    assert detail.json()["current"]
    assert detail.json()["snapshot"]["product"]["name"] == product["name"]
    assert detail.json()["result"]["decisions"][0]["relevant"]
    assert (
        admin_client.get(ROOT + "/evaluations", params={"provider": "missing"}).json()["total"] == 0
    )
    assert admin_client.get(ROOT + "/evaluations/" + str(uuid.uuid4())).status_code == 404
    admin_client.post(
        ROOT + f"/{product['id']}/actions",
        json={"action": "exclude", "expected_revision": product["revision"]},
    )
    assert not admin_client.get(ROOT + "/evaluations/" + identifier).json()["current"]
    assert admin_client.get(ROOT + "/evaluations").json()["total"] == 1

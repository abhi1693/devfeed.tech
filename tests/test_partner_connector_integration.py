"""Generic connectors share the catalog pipeline and revision/authorization fences."""

import uuid

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline
from devfeed_core import partner_connectors as connectors
from devfeed_core.models import PartnerConnection, PartnerPipelineJob, PartnerProduct
from sqlalchemy import func, select
from test_partner_tools_integration import finish_sync

pytestmark = pytest.mark.integration
ROOT = "/v1/admin/partner-tools"
CONFIG = {
    "base_url": "https://api.platform.example",
    "list_path": "/v2/products",
    "items_paths": ["data.products"],
    "pagination": {"mode": "none"},
    "listing_url_template": "https://platform.example/products/{id}",
}
RECORD = {
    "id": "checker",
    "name": "API Checker",
    "url": "https://checker.example",
    "description": "Check API compatibility before deploying software.",
}


def create(client, **changes):
    response = client.post(
        ROOT + "/connections",
        json={"provider": "platform", "name": "Custom Platform", "connector": CONFIG, **changes},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_custom_definition_runs_existing_catalog_and_ai_pipeline(
    admin_client, database, monkeypatch
):
    requests = []
    monkeypatch.setattr(
        connectors,
        "api_json",
        lambda provider, config, url: (
            requests.append((provider, url)) or {"data": {"products": [RECORD]}}
        ),
    )
    created = create(admin_client)
    assert created["name"] == "Custom Platform" and created["state"] == "syncing"
    with database() as session:
        parent = session.scalar(
            select(PartnerPipelineJob).where(PartnerPipelineJob.operation == "sync")
        )
        identifier = str(parent.id)
        snapshot = parent.payload["connector"]
    assert snapshot == created["connector"]
    process_pipeline(identifier, factory=database)
    finish_sync(database, identifier)
    products = admin_client.get(ROOT).json()["items"]
    assert len(products) == 1
    assert products[0]["name"] == RECORD["name"]
    assert products[0]["listings"][0]["platform_name"] == "Custom Platform"
    assert products[0]["eligible"] is False and products[0]["status"] == "pending"
    assert requests == [("platform", "https://api.platform.example/v2/products?limit=10")]
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(identifier)).status == "succeeded"
        assert (
            session.scalar(
                select(PartnerPipelineJob).where(PartnerPipelineJob.operation == "assess")
            )
            is not None
        )
    assert admin_client.get(ROOT + "/connections/platform/jobs").status_code == 200
    assert (
        admin_client.post(ROOT + "/connections/platform", json={"action": "pause"}).status_code
        == 200
    )


def test_definition_edit_cancels_generation_and_saves_new_snapshot(
    admin_client, database, monkeypatch
):
    created = create(admin_client)
    with database() as session:
        old = session.scalar(select(PartnerPipelineJob))
        old_id = old.id
    updated_config = {**CONFIG, "list_path": "/v3/products"}
    response = admin_client.put(
        ROOT + "/connections/platform",
        json={
            "enabled": True,
            "expected_revision": created["revision"],
            "connector": updated_config,
        },
    )
    assert response.status_code == 200, response.text
    with database() as session:
        jobs = session.scalars(
            select(PartnerPipelineJob).order_by(PartnerPipelineJob.created_at)
        ).all()
        assert len(jobs) == 2 and jobs[0].status == "failed" and jobs[1].status == "queued"
        assert jobs[0].payload["connector"]["list_path"] == "/v2/products"
        assert jobs[1].payload["connector"]["list_path"] == "/v3/products"
        assert (
            jobs[1].payload["connection_revision"]
            == session.get(PartnerConnection, "platform").sync_revision
        )
    process_pipeline(
        str(old_id), factory=database, reader=lambda _: pytest.fail("Old generation must not run")
    )
    stale = admin_client.put(
        ROOT + "/connections/platform",
        json={"enabled": False, "expected_revision": created["revision"]},
    )
    assert stale.status_code == 409


def test_preview_is_bounded_read_only_and_sanitizes_errors(admin_client, database, monkeypatch):
    calls = []
    config = {**CONFIG, "detail_path": "/products/{id}"}

    def api_json(provider, config, url):
        calls.append(url)
        if "/v2/products" in url:
            return {"data": {"products": [{**RECORD, "id": str(index)} for index in range(10)]}}
        if url.endswith("/1"):
            raise ValueError("sensitive private upstream response")
        return {**RECORD, "id": url.rsplit("/", 1)[1]}

    monkeypatch.setattr(connectors, "api_json", api_json)
    response = admin_client.post(
        ROOT + "/connector-preview", json={"provider": "platform", "connector": config}
    )
    assert response.status_code == 200, response.text
    assert response.json()["discovered"] == 10
    assert len(response.json()["products"]) == 2 and len(response.json()["errors"]) == 1
    assert len(calls) == 4 and "sensitive" not in response.text
    with database() as session:
        for model in (PartnerConnection, PartnerPipelineJob, PartnerProduct):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(
        connectors, "api_json", lambda *_: (_ for _ in ()).throw(ValueError("private secret"))
    )
    response = admin_client.post(
        ROOT + "/connector-preview", json={"provider": "platform", "connector": config}
    )
    assert response.status_code == 422 and "private secret" not in response.text


def test_invalid_definitions_do_not_persist(admin_client, database):
    for config in [
        {**CONFIG, "base_url": "https://127.0.0.1"},
        {**CONFIG, "list_path": "//evil.example/products"},
    ]:
        response = admin_client.post(
            ROOT + "/connections",
            json={"provider": "platform", "name": "Custom Platform", "connector": config},
        )
        assert response.status_code == 422
    assert (
        admin_client.post(
            ROOT + "/connections",
            json={"provider": "../invalid", "name": "Invalid", "connector": CONFIG},
        ).status_code
        == 422
    )


def test_page_cap_does_not_complete_partial_import(admin_client, database, monkeypatch):
    from datetime import timedelta

    from devfeed_core.models import utcnow

    config = {**CONFIG, "max_pages": 1, "pagination": {"mode": "cursor", "next_path": "next"}}
    create(admin_client, connector=config)
    monkeypatch.setattr(
        connectors, "api_json", lambda *_: {"data": {"products": [RECORD]}, "next": "another-page"}
    )
    with database() as session:
        identifier = session.scalar(select(PartnerPipelineJob.id))
    for _ in range(4):
        process_pipeline(str(identifier), factory=database)
        with database.begin() as session:
            session.get(PartnerPipelineJob, identifier).available_at = utcnow() - timedelta(
                seconds=1
            )
    with database() as session:
        job = session.get(PartnerPipelineJob, identifier)
        assert job.status == "failed" and job.payload["pages"] == 0
        assert not job.payload.get("discovery_complete")
        assert session.scalar(select(func.count()).select_from(PartnerProduct)) == 0
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 1
        assert session.get(PartnerConnection, "platform").last_sync_at is None


def test_raw_response_preview_does_not_require_known_mapping(admin_client, database, monkeypatch):
    monkeypatch.setattr(
        connectors,
        "api_json",
        lambda *_: {"unknown": {"products": [RECORD]}, "access_token": "sensitive"},
    )
    response = admin_client.post(
        ROOT + "/connector-response", json={"provider": "platform", "connector": CONFIG}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["unknown"]["products"][0]["name"] == RECORD["name"]
    assert response.json()["data"]["access_token"] == "[redacted]"
    assert admin_client.get(ROOT + "/connections").json() == []
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 0

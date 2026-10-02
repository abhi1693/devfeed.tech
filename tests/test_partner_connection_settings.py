"""Supported partner creation and revision-protected connection settings."""

import pytest
from devfeed_core.models import PartnerConnection, PartnerPipelineJob
from sqlalchemy import select
from test_partner_tools import product_payload as shared_product_payload

product_payload = shared_product_payload

pytestmark = pytest.mark.integration
ROOT = "/v1/admin/partner-tools"
PATH = ROOT + "/connections"
PROVIDER = "nick-launches"


def test_supported_catalog_and_create_disabled(admin_client, database):
    providers = admin_client.get(ROOT + "/providers").json()
    assert [p["provider"] for p in providers] == [PROVIDER]
    assert admin_client.get(PATH).json() == []
    response = admin_client.post(PATH, json={"provider": PROVIDER, "enabled": False})
    assert response.status_code == 201, response.text
    assert response.json()["enabled"] is False
    assert response.json()["revision"] == 1
    assert len(admin_client.get(PATH).json()) == 1
    with database() as session:
        assert session.scalar(select(PartnerPipelineJob)) is None
    assert admin_client.post(PATH, json={"provider": PROVIDER}).status_code == 409
    assert admin_client.post(PATH, json={"provider": "unsupported"}).status_code == 422
    assert (
        admin_client.post(
            PATH, json={"provider": PROVIDER, "api_url": "https://example.com"}
        ).status_code
        == 422
    )


def test_enable_disable_and_stale_settings(admin_client, database):
    created = admin_client.post(PATH, json={"provider": PROVIDER, "enabled": False}).json()
    enabled = admin_client.put(
        PATH + "/" + PROVIDER, json={"enabled": True, "expected_revision": created["revision"]}
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] and enabled.json()["state"] == "syncing"
    revision = enabled.json()["revision"]
    same = admin_client.put(
        PATH + "/" + PROVIDER, json={"enabled": True, "expected_revision": revision}
    )
    assert same.json()["revision"] == revision
    stale = admin_client.put(
        PATH + "/" + PROVIDER, json={"enabled": False, "expected_revision": created["revision"]}
    )
    assert stale.status_code == 409
    with database() as session:
        assert session.get(PartnerConnection, PROVIDER).enabled
        assert len(session.scalars(select(PartnerPipelineJob)).all()) == 1
    disabled = admin_client.put(
        PATH + "/" + PROVIDER, json={"enabled": False, "expected_revision": revision}
    )
    assert disabled.status_code == 200 and not disabled.json()["enabled"]
    assert disabled.json()["next_sync_at"] is None
    with database() as session:
        assert session.scalar(select(PartnerPipelineJob)).status not in {"queued", "running"}
    assert admin_client.post(PATH + "/" + PROVIDER, json={"action": "sync"}).status_code == 409


def test_default_creation_starts_sync_and_unknown_edit_is_rejected(admin_client, database):
    response = admin_client.post(PATH, json={"provider": PROVIDER})
    assert response.status_code == 201 and response.json()["state"] == "syncing"
    with database() as session:
        assert session.scalar(select(PartnerPipelineJob)).operation == "sync"
    assert (
        admin_client.put(
            PATH + "/unknown", json={"enabled": True, "expected_revision": 1}
        ).status_code
        == 404
    )


def test_related_products_and_jobs_are_scoped_and_paginated(
    admin_client, database, product_payload
):
    from devfeed_core.models import PartnerListing, PartnerProduct
    from test_partner_tools_integration import synced

    product = synced(admin_client, database, product_payload)
    with database.begin() as session:
        session.add(PartnerConnection(provider="platform-b", enabled=True))
        unrelated = PartnerProduct(
            name="Unrelated", product_url="https://unrelated.example", description="Unrelated"
        )
        session.add(unrelated)
        session.flush()
        session.add(
            PartnerListing(
                product_id=unrelated.id,
                provider="platform-b",
                external_id="unrelated",
                name="Unrelated",
                product_url=unrelated.product_url,
                listing_url="https://platform-b.example/unrelated",
                description="Unrelated",
            )
        )
        session.add(PartnerPipelineJob(provider="platform-b", operation="sync"))
    result = admin_client.get(ROOT, params={"provider": PROVIDER}).json()
    assert result["total"] == 1 and result["items"][0]["id"] == product["id"]
    assert admin_client.get(ROOT, params={"provider": "missing"}).json()["total"] == 0
    result = admin_client.get(ROOT, params={"product_id": product["id"]}).json()
    assert result["total"] == 1
    with database.begin() as session:
        assessment = session.scalar(
            select(PartnerPipelineJob).where(PartnerPipelineJob.operation == "assess")
        )
        assessment.provider = "platform-b"
    jobs = admin_client.get(PATH + "/" + PROVIDER + "/jobs", params={"limit": 1}).json()
    assert jobs["total"] == 3 and len(jobs["items"]) == 1
    assert jobs["items"][0]["provider"] == "platform-b"
    assert jobs["items"][0]["operation"] == "assess"
    assert admin_client.get(PATH + "/missing/jobs").status_code == 404

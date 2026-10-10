"""Per-partner schedules change without cancelling active product syncs."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.partner_sync_tasks import process_pipeline, schedule_partner_syncs
from devfeed_core.models import PartnerConnection, PartnerPipelineJob, utcnow
from devfeed_core.partner_tools import ProductInput
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload
from test_partner_tools_integration import CONNECTION, ROOT, finish_sync, job_id, synced

product_payload = shared_product_payload
pytestmark = pytest.mark.integration


def settings(client, **values):
    connection = client.get(ROOT + "/connections").json()[0]
    return client.put(
        CONNECTION,
        json={
            "enabled": connection["enabled"],
            "expected_revision": connection["revision"],
            **values,
        },
    )


def test_default_custom_disabled_and_validation(admin_client, database):
    response = admin_client.post(
        ROOT + "/connections", json={"provider": "nick-launches", "enabled": False}
    )
    assert response.status_code == 201 and response.json()["sync_interval_minutes"] == 360
    changed = settings(admin_client, sync_interval_minutes=45)
    assert changed.status_code == 200 and changed.json()["sync_interval_minutes"] == 45
    assert changed.json()["next_sync_at"] is None
    with database() as session:
        assert session.scalar(select(PartnerPipelineJob)) is None
    for invalid in (0, -1, 10081, 15.5, True):
        assert settings(admin_client, sync_interval_minutes=invalid).status_code == 422
    # Old clients that omit the new field preserve the chosen interval.
    assert settings(admin_client).json()["sync_interval_minutes"] == 45
    enabled = settings(admin_client, enabled=True).json()
    with database() as session:
        scheduled = session.get(PartnerConnection, "nick-launches").next_sync_at
    assert timedelta(minutes=44) < scheduled - utcnow() <= timedelta(minutes=45)
    assert enabled["sync_interval_minutes"] == 45


def test_interval_change_recalculates_next_sync_and_is_partner_specific(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_admin_api import partner_tools

    synced(admin_client, database, product_payload)
    now = utcnow()
    last = now - timedelta(hours=2)
    other_due = now + timedelta(hours=5)
    with database.begin() as session:
        session.get(PartnerConnection, "nick-launches").last_sync_at = last
        session.add(
            PartnerConnection(
                provider="platform-b",
                enabled=True,
                sync_interval_minutes=30,
                next_sync_at=other_due,
            )
        )
        before = session.scalar(select(func.count()).select_from(PartnerPipelineJob))
    monkeypatch.setattr(partner_tools, "utcnow", lambda: now)
    assert settings(admin_client, sync_interval_minutes=1440).status_code == 200
    with database() as session:
        assert session.get(PartnerConnection, "nick-launches").next_sync_at == last + timedelta(
            days=1
        )
        other = session.get(PartnerConnection, "platform-b")
        assert other.sync_interval_minutes == 30 and other.next_sync_at == other_due
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == before
    assert settings(admin_client, sync_interval_minutes=60).status_code == 200
    with database() as session:
        assert session.get(PartnerConnection, "nick-launches").next_sync_at == now
    schedule_partner_syncs(database)
    with database() as session:
        jobs = session.scalars(
            select(PartnerPipelineJob).where(
                PartnerPipelineJob.operation == "sync", PartnerPipelineJob.status == "queued"
            )
        ).all()
        assert len(jobs) == 1 and jobs[0].provider == "nick-launches"


def test_interval_edits_during_discovery_and_product_fetch_keep_run_valid(
    admin_client, database, product_payload
):
    created = admin_client.post(
        ROOT + "/connections", json={"provider": "nick-launches", "sync_interval_minutes": 30}
    ).json()
    parent = job_id(database, "sync")
    item = ProductInput.model_validate(product_payload)

    def discovery(_):
        assert settings(admin_client, sync_interval_minutes=60).status_code == 200
        return [item], None

    process_pipeline(parent, factory=database, reader=discovery)
    with database() as session:
        child = str(
            session.scalar(
                select(PartnerPipelineJob.id).where(
                    PartnerPipelineJob.parent_id == uuid.UUID(parent)
                )
            )
        )

    def fetch_product(provider, candidate):
        assert settings(admin_client, sync_interval_minutes=90).status_code == 200
        return ProductInput.model_validate(candidate.data)

    process_pipeline(child, factory=database, product_reader=fetch_product)
    finish_sync(database, parent)
    with database() as session:
        connection = session.get(PartnerConnection, "nick-launches")
        assert connection.revision == created["revision"] + 2
        assert connection.next_sync_at - connection.last_sync_at - timedelta(
            minutes=90
        ) < timedelta(seconds=1)
        assert connection.next_sync_at - connection.last_sync_at >= timedelta(minutes=90)
        assert session.get(PartnerPipelineJob, uuid.UUID(parent)).status == "succeeded"
        child_job = session.get(PartnerPipelineJob, uuid.UUID(child))
        assert child_job.status == "succeeded" and child_job.attempts == 1
    stale = admin_client.put(
        CONNECTION,
        json={
            "enabled": True,
            "expected_revision": created["revision"],
            "sync_interval_minutes": 120,
        },
    )
    assert stale.status_code == 409

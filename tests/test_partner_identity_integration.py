"""Cross-platform identity, shared qualification, and independent source lifecycle."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core.models import (
    PartnerConnection,
    PartnerEvaluation,
    PartnerListing,
    PartnerPipelineJob,
    PartnerProduct,
    PartnerProductURL,
)
from devfeed_core.partner_catalog import (
    lock_catalog,
    record_verified_redirect,
    withdraw_missing_listings,
)
from devfeed_core.partner_connections import upsert_partner_product
from devfeed_core.partner_tools import ProductInput
from sqlalchemy import func, select
from test_partner_tools import product_payload as shared_product_payload
from test_partner_tools_integration import CONNECTION, ROOT, article, qualified

product_payload = shared_product_payload
pytestmark = pytest.mark.integration


def second_platform(database, payload, **changes):
    with database.begin() as session:
        lock_catalog(session)
        if not session.get(PartnerConnection, "platform-b"):
            session.add(PartnerConnection(provider="platform-b", enabled=True))
            session.flush()
        item = ProductInput.model_validate(
            {
                **payload,
                "provider": "platform-b",
                "external_id": "other-id",
                "listing_url": "https://platform-b.example/tools/checker/",
                "attribution": "Via Platform B",
                **changes,
            }
        )
        result = upsert_partner_product(session, item, uuid.uuid4())
        session.flush()
        return str(result.id)


def test_tracking_variants_share_identity_qualification_and_attribution(
    admin_client, database, product_payload, monkeypatch
):
    article(database)
    product = qualified(admin_client, database, product_payload, monkeypatch)
    second = second_platform(
        database,
        product_payload,
        product_url=product_payload["product_url"] + "?utm_source=platform-b",
        description="A different platform's description of the same product.",
    )
    assert second == product["id"]
    page = admin_client.get(ROOT).json()
    assert page["total"] == 1
    current = page["items"][0]
    assert current["revision"] == product["revision"] and current["eligible"]
    assert current["description"] == product_payload["description"]
    assert {listing["provider"] for listing in current["listings"]} == {
        "nick-launches",
        "platform-b",
    }
    assert current["listings"][1]["description"] != current["description"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerEvaluation)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerPipelineJob)
                .where(PartnerPipelineJob.operation == "assess")
            )
            == 1
        )


def test_withdrawal_is_per_listing_and_exclusion_is_global(
    admin_client, database, product_payload, monkeypatch
):
    product = qualified(admin_client, database, product_payload, monkeypatch)
    second_platform(database, product_payload)
    with database.begin() as session:
        lock_catalog(session)
        assert withdraw_missing_listings(session, "nick-launches", uuid.uuid4()) == 1
    current = admin_client.get(ROOT).json()["items"][0]
    assert current["eligible"] and current["revision"] == product["revision"]
    assert not current["listings"][0]["active"] and current["listings"][1]["active"]
    response = admin_client.post(
        f"{ROOT}/{product['id']}/actions",
        json={"action": "exclude", "expected_revision": product["revision"]},
    )
    assert response.status_code == 200
    second_platform(
        database, product_payload, description="New platform metadata must not clear exclusion."
    )
    assert admin_client.get(ROOT).json()["items"][0]["excluded"]
    with database.begin() as session:
        lock_catalog(session)
        withdraw_missing_listings(session, "platform-b", uuid.uuid4())
    current = admin_client.get(ROOT).json()["items"][0]
    assert current["status"] == "withdrawn" and not current["eligible"]
    second_platform(database, product_payload)
    assert admin_client.get(ROOT).json()["items"][0]["excluded"]


def test_pausing_one_platform_preserves_shared_assessment(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_aggregator.partner_sync_tasks import process_pipeline
    from devfeed_core.config import get_settings
    from test_partner_tools_integration import job_id, synced

    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    product = synced(admin_client, database, product_payload)
    identifier = job_id(database, "assess")
    second_platform(database, product_payload)
    assert admin_client.post(CONNECTION, json={"action": "pause"}).status_code == 200

    def assess(*_):
        return {
            "decision": "uncertain",
            "reason": "The official page lacks sufficient evidence.",
            "technologies": [],
            "evidence": [],
        }

    process_pipeline(
        identifier,
        factory=database,
        page_fetcher=lambda *_: {"final_url": product["product_url"], "text": "Some text"},
        assessor=assess,
    )
    with database() as session:
        assert session.get(PartnerPipelineJob, uuid.UUID(identifier)).status == "succeeded"
    assert admin_client.get(ROOT).json()["items"][0]["assessment"]["state"] == "attention"


@pytest.mark.parametrize(
    "url",
    [
        "https://checker.example/another-product",
        "https://checker.example/?product=other",
        "https://different.example/",
    ],
)
def test_names_and_domains_do_not_merge_distinct_urls(
    admin_client, database, product_payload, monkeypatch, url
):
    product = qualified(admin_client, database, product_payload, monkeypatch)
    assert second_platform(database, product_payload, product_url=url) != product["id"]
    assert admin_client.get(ROOT).json()["total"] == 2


def test_verified_www_redirect_merges_and_preserves_exclusion(
    admin_client, database, product_payload, monkeypatch
):
    original = qualified(admin_client, database, product_payload, monkeypatch)
    admin_client.post(
        f"{ROOT}/{original['id']}/actions",
        json={"action": "exclude", "expected_revision": original["revision"]},
    )
    other = second_platform(database, product_payload, product_url="https://www.checker.example/")
    assert other != original["id"]
    with database.begin() as session:
        lock_catalog(session)
        donor = session.get(PartnerProduct, uuid.UUID(other))
        merged = record_verified_redirect(session, donor, "https://checker.example/")
        assert str(merged.id) == original["id"] and merged.excluded
    page = admin_client.get(ROOT).json()
    assert page["total"] == 1 and len(page["items"][0]["listings"]) == 2
    assert not page["items"][0]["eligible"]
    assert (
        second_platform(database, product_payload, product_url="https://www.checker.example/")
        == original["id"]
    )
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerProductURL)) == 2
        assert not session.scalar(
            select(PartnerPipelineJob.id).where(
                PartnerPipelineJob.operation == "assess",
                PartnerPipelineJob.status.in_(["queued", "running"]),
            )
        )


def test_changed_unverified_destination_stays_unresolved(
    admin_client, database, product_payload, monkeypatch
):
    original = qualified(admin_client, database, product_payload, monkeypatch)
    second_platform(database, product_payload)
    second_platform(database, product_payload, product_url="https://unrelated.example/")
    page = admin_client.get(ROOT).json()
    assert page["total"] == 1 and page["items"][0]["eligible"]
    assert page["items"][0]["id"] == original["id"]
    assert page["items"][0]["listings"][1]["identity_status"] == "unresolved"


def test_concurrent_platform_syncs_create_one_product(database, product_payload):
    with database.begin() as session:
        session.add_all(
            [PartnerConnection(provider=p, enabled=True) for p in ("nick-launches", "platform-b")]
        )

    def sync(provider):
        with database.begin() as session:
            item = ProductInput.model_validate({**product_payload, "provider": provider})
            return upsert_partner_product(session, item, uuid.uuid4()).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        identities = list(executor.map(sync, ["nick-launches", "platform-b"]))
    assert identities[0] == identities[1]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerProduct)) == 1
        assert session.scalar(select(func.count()).select_from(PartnerListing)) == 2
        assert session.scalar(select(func.count()).select_from(PartnerPipelineJob)) == 1


@pytest.mark.parametrize(
    "statuses", [("approved", "approved"), ("withdrawn", "approved"), ("withdrawn", "withdrawn")]
)
def test_existing_provider_products_migrate_without_losing_listings(database, statuses):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from devfeed_core.models import utcnow
    from sqlalchemy import MetaData, Table, text

    def migration(filename):
        path = Path(__file__).parents[1] / "migrations" / "versions" / filename
        spec = importlib.util.spec_from_file_location("partner_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # Exercise the actual data migration in an isolated schema on the disposable test DB.
    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA partner_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO partner_migration_fixture"))
            with Operations.context(MigrationContext.configure(connection)):
                migration("0021_partner_tools.py").upgrade()
                migration("0022_partner_connections.py").upgrade()
                meta = MetaData()
                products = Table("partner_products", meta, autoload_with=connection)
                connections = Table("partner_connections", meta, autoload_with=connection)
                ids = [uuid.uuid4(), uuid.uuid4()]
                for index, provider in enumerate(["nick-launches", "platform-b"]):
                    connection.execute(
                        connections.insert().values(
                            provider=provider,
                            enabled=True,
                            revision=1,
                            next_sync_at=utcnow(),
                            updated_by={},
                        )
                    )
                    connection.execute(
                        products.insert().values(
                            id=ids[index],
                            provider=provider,
                            external_id="checker",
                            name="API Checker",
                            product_url="https://checker.example/"
                            + ("?utm_source=b" if index else ""),
                            listing_url=f"https://{provider}.example/checker",
                            description=f"Description from platform {index}",
                            pricing="free",
                            technologies=[],
                            evidence=[],
                            attribution=provider,
                            revision=1,
                            status=statuses[index],
                            verified_at=utcnow(),
                            updated_at=utcnow(),
                            reviews=[],
                            connection_id=provider,
                            excluded=bool(index),
                        )
                    )
                migration("0023_partner_product_identity.py").upgrade()
            products = Table("partner_products", MetaData(), autoload_with=connection)
            listings = Table("partner_listings", MetaData(), autoload_with=connection)
            canonical = (
                connection.execute(select(products).where(products.c.merged_into_id.is_(None)))
                .mappings()
                .one()
            )
            assert canonical["id"] == ids[0] and canonical["excluded"]
            assert canonical["verified_at"] is None
            assert canonical["status"] == (
                "withdrawn" if all(status == "withdrawn" for status in statuses) else "paused"
            )
            rows = connection.execute(select(listings)).mappings().all()
            assert len(rows) == 2 and {row["product_id"] for row in rows} == {ids[0]}
            assert {row["description"] for row in rows} == {
                "Description from platform 0",
                "Description from platform 1",
            }
            assert "provider" not in products.c and "external_id" not in products.c
        finally:
            transaction.rollback()


def test_worker_uses_verified_alias_before_repeating_ai_assessment(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_aggregator.partner_sync_tasks import process_pipeline
    from test_partner_tools_integration import job_id

    original = qualified(admin_client, database, product_payload, monkeypatch)
    second_platform(database, product_payload, product_url="https://www.checker.example/")
    identifier = job_id(database, "assess")
    process_pipeline(
        identifier,
        factory=database,
        page_fetcher=lambda *_: {
            "final_url": "https://checker.example/",
            "text": "Official website",
        },
        assessor=lambda *_: pytest.fail("Existing qualification should be reused"),
    )
    page = admin_client.get(ROOT).json()
    assert page["total"] == 1 and page["items"][0]["eligible"]
    assert page["items"][0]["revision"] == original["revision"]


def test_changed_url_resolves_automatically_when_the_old_site_redirects(
    admin_client, database, product_payload, monkeypatch
):
    from devfeed_aggregator.partner_sync_tasks import process_pipeline
    from test_partner_tools_integration import finish_sync, job_id

    original = qualified(admin_client, database, product_payload, monkeypatch)
    assert admin_client.post(CONNECTION, json={"action": "sync"}).status_code == 200
    updated = ProductInput.model_validate(
        {**product_payload, "product_url": "https://www.checker.example/"}
    )
    sync_id = job_id(database, "sync")
    process_pipeline(
        sync_id,
        factory=database,
        reader=lambda _: ([updated], None),
        page_fetcher=lambda *_: {"final_url": updated.product_url, "text": "Verified redirect"},
    )
    finish_sync(
        database,
        sync_id,
        page_fetcher=lambda *_: {"final_url": updated.product_url, "text": "Verified redirect"},
    )
    current = admin_client.get(ROOT).json()["items"][0]
    assert current["id"] == original["id"] and current["eligible"]
    assert current["listings"][0]["identity_status"] == "resolved"
    assert current["revision"] == original["revision"]

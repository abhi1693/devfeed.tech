"""Official product icons use bounded discovery and the durable managed-image outbox."""

import importlib
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from devfeed_aggregator import image_storage_tasks, image_tasks, logo_storage, product_logo_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.image_jobs import retry_image
from devfeed_core.logos import backfill_logos, request_logo
from devfeed_core.models import ArticleImageJob, PartnerListing, PartnerProduct, utcnow
from devfeed_core.partner_product_logos import product_icon_urls, product_logo_url
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver, query

PAGE = "https://official.example/products/tool"


def page(body, *, status=200):
    return FetchResult(status, body.encode(), PAGE, content_type="text/html")


def test_discovery_prefers_touch_icons_deduplicates_and_uses_official_favicon():
    result = page(
        '<link rel="shortcut icon" href="/logo.svg">'
        '<link rel="apple-touch-icon" href="/touch.png">'
        '<link rel="icon" href="/logo.svg">'
    )
    assert product_icon_urls(result) == [
        "https://official.example/touch.png",
        "https://official.example/logo.svg",
        "https://official.example/favicon.ico",
    ]


@pytest.mark.parametrize(
    "href",
    [
        "http://official.example/icon.png",
        "data:image/png,abc",
        "javascript:alert(1)",
        "https://127.0.0.1/icon.png",
    ],
)
def test_discovery_discards_unsafe_icons_and_ignores_inert_or_non_icon_markup(href):
    result = page(
        f'<link rel="icon" href="{href}">'
        '<template><link rel="icon" href="/template.png"></template>'
        '<noscript><link rel="icon" href="/noscript.png"></noscript>'
        '<link rel="preload" href="/photo.png">'
        '<meta property="og:image" content="/photo.png">'
    )
    assert product_icon_urls(result) == ["https://official.example/favicon.ico"]


def test_discovery_bounds_candidates_and_does_not_follow_html_base():
    result = page(
        '<base href="https://unrelated.example/">'
        + "".join(f'<link rel="icon" href="/{index}.png">' for index in range(100))
    )
    assert product_icon_urls(result) == [
        f"https://official.example/{index}.png" for index in range(3)
    ]


@pytest.mark.parametrize("retryable", [False, True])
def test_official_discovery_falls_back_only_after_permanent_icon_failure(monkeypatch, retryable):
    monkeypatch.setattr(
        product_logo_storage, "fetch_page", lambda url: page('<link rel="icon" href="/icon.svg">')
    )
    failure = FeedError("safe error", reason="invalid_topic_logo", retryable=retryable)
    store = Mock(side_effect=[failure, {"hash": "hash", "variants": []}])
    monkeypatch.setattr(product_logo_storage, "store_logo_original", store)
    if retryable:
        with pytest.raises(FeedError) as raised:
            product_logo_storage.store_product_logo_original(None, PAGE)
        assert raised.value is failure and store.call_count == 1
    else:
        result = product_logo_storage.store_product_logo_original(None, PAGE)
        assert store.call_count == 2
        assert result["source_url"] == PAGE
        assert result["icon_url"] == "https://official.example/favicon.ico"


@pytest.mark.parametrize("status,body", [(404, "missing"), (200, "")])
def test_unavailable_official_pages_do_not_trigger_icon_fetches(monkeypatch, status, body):
    monkeypatch.setattr(product_logo_storage, "fetch_page", lambda url: page(body, status=status))
    store = Mock()
    monkeypatch.setattr(product_logo_storage, "store_logo_original", store)
    with pytest.raises(FeedError, match="Product site unavailable"):
        product_logo_storage.store_product_logo_original(None, PAGE)
    store.assert_not_called()


def test_public_logo_uses_managed_assets_and_fences_changed_product_identity(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example")
    get_settings.cache_clear()
    product = PartnerProduct(name="Tool", product_url=PAGE, description="A tool.", managed_logo={})
    assert product_logo_url(product) is None
    product.managed_logo = {
        "source_url": PAGE,
        "icon_url": "https://remote.example/icon.svg",
        "original_key": "originals/hash.png",
        "version": "v1",
        "variants": [],
    }
    assert product_logo_url(product) == "https://images.example/originals/hash.png"
    product.managed_logo["variants"] = [
        {"key": f"logos/hash/{size}.webp", "width": size} for size in (32, 64, 96)
    ]
    assert product_logo_url(product) == "https://images.example/logos/hash/64.webp"
    product.product_url = "https://replacement.example/tool"
    assert product_logo_url(product) is None


def migration_sql(direction):
    migration = importlib.import_module("migrations.versions.0029_partner_product_logos")
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()
    return output.getvalue()


def test_product_logo_migration_offline_sql_preserves_other_image_subjects():
    upgrade = migration_sql("upgrade")
    assert "managed_logo JSONB DEFAULT '{}' NOT NULL" in upgrade
    assert "FOREIGN KEY(product_id) REFERENCES partner_products (id) ON DELETE CASCADE" in upgrade
    assert "CREATE UNIQUE INDEX uq_product_image_active" in upgrade
    assert "WHERE status IN ('queued','running')" in upgrade
    assert "product_id IS NOT NULL AND operation = 'product-logo'" in upgrade
    downgrade = migration_sql("downgrade")
    assert (
        downgrade.index("DELETE FROM article_image_jobs WHERE product_id IS NOT NULL")
        < downgrade.index("ADD CONSTRAINT ck_image_job_subject CHECK")
        < downgrade.index("DROP COLUMN product_id")
    )
    assert "DROP INDEX uq_product_image_active" in downgrade
    assert "ALTER TABLE partner_products DROP COLUMN managed_logo" in downgrade


@pytest.fixture
def logo_catalog(database, catalog, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example")
    get_settings.cache_clear()
    monkeypatch.setattr(
        image_storage_tasks, "storage_client", lambda: SimpleNamespace(close=lambda: None)
    )
    original = Mock(
        side_effect=lambda client, source: {
            "source_url": source,
            "icon_url": "https://official.example/icon.svg",
            "hash": "product-hash",
            "original_key": "originals/product-hash.png",
            "version": "v1",
            "variants": [],
        }
    )
    variant = Mock(
        side_effect=lambda client, asset, size: {
            "key": f"logos/product-hash/{size}.webp",
            "width": size,
        }
    )
    monkeypatch.setattr(product_logo_storage, "store_product_logo_original", original)
    monkeypatch.setattr(logo_storage, "store_logo_variant", variant)
    return catalog, original, variant


def request_product_job(database, identifier):
    with database.begin() as session:
        return request_logo(session, PartnerProduct, identifier).id


def process(job_id):
    image_tasks.enrich_image(str(job_id))


@pytest.mark.integration
def test_product_queue_coalesces_and_publication_uses_shared_32_64_96_variants(
    database, logo_catalog
):
    ids, original, variant = logo_catalog
    identifier = ids["free_product"]
    job_id = request_product_job(database, identifier)
    assert request_product_job(database, identifier) == job_id
    with database() as session:
        job = session.get(ArticleImageJob, job_id)
        assert job.operation == "product-logo" and job.product_id == identifier
        assert job.article_id is job.source_id is job.topic_id is None
    process(job_id)
    with database() as session:
        product = session.get(PartnerProduct, identifier)
        job = session.get(ArticleImageJob, job_id)
        assert job.status == "queued" and not job.storage["variants"]
        assert product_logo_url(product) == "https://images.example/originals/product-hash.png"
    process(job_id)
    assert original.call_count == 1
    assert [call.args[2] for call in variant.call_args_list] == [32, 64, 96]
    with database.begin() as session:
        product = session.get(PartnerProduct, identifier)
        assert product_logo_url(product) == "https://images.example/logos/product-hash/64.webp"
        assert session.get(ArticleImageJob, job_id).status == "succeeded"
        assert request_logo(session, PartnerProduct, identifier) is None
    placements = deliver(database, query(ids))
    assert (
        next(row for row in placements if row["product_id"] == identifier)["logo_url"]
        == "https://images.example/logos/product-hash/64.webp"
    )


@pytest.mark.integration
@pytest.mark.parametrize("retryable", [False, True])
def test_product_checkpoint_retry_keeps_completed_sizes_and_resumes_without_refetch(
    database, logo_catalog, monkeypatch, retryable
):
    ids, original, _ = logo_catalog
    calls = []

    def variant(client, asset, size):
        calls.append(size)
        if size == 64 and calls.count(size) == 1:
            raise FeedError("transform failed", reason="image_transform", retryable=retryable)
        return {"key": f"logos/product-hash/{size}.webp", "width": size}

    monkeypatch.setattr(logo_storage, "store_logo_variant", variant)
    identifier = ids["free_product"]
    job_id = request_product_job(database, identifier)
    process(job_id)
    process(job_id)
    with database.begin() as session:
        old = session.get(ArticleImageJob, job_id)
        assert [row["width"] for row in old.storage["variants"]] == [32]
        assert product_logo_url(session.get(PartnerProduct, identifier)).endswith(
            "product-hash.png"
        )
        if retryable:
            assert old.status == "queued"
            old.available_at = utcnow() - timedelta(seconds=1)
            resumed_id = old.id
        else:
            assert old.status == "failed"
            resumed = retry_image(session, old.id)
            assert resumed.id != old.id and resumed.storage == old.storage
            resumed_id = resumed.id
    process(resumed_id)
    assert calls == [32, 64, 64, 96]
    assert original.call_count == 1
    with database() as session:
        assert session.get(ArticleImageJob, resumed_id).status == "succeeded"
        assert product_logo_url(session.get(PartnerProduct, identifier)).endswith("64.webp")


@pytest.mark.integration
def test_product_url_change_during_import_cannot_publish_stale_branding(
    database, logo_catalog, monkeypatch
):
    ids, original, _ = logo_catalog
    identifier = ids["free_product"]
    job_id = request_product_job(database, identifier)
    replacement = "https://replacement.example/product"

    def change(client, source):
        with database.begin() as session:
            session.get(PartnerProduct, identifier).product_url = replacement
        return original(client, source)

    monkeypatch.setattr(product_logo_storage, "store_product_logo_original", change)
    process(job_id)
    process(job_id)
    with database() as session:
        product = session.get(PartnerProduct, identifier)
        assert product_logo_url(product) is None
        assert session.get(ArticleImageJob, job_id).outcome == "already_present"
        next_job = session.scalar(
            select(ArticleImageJob).where(
                ArticleImageJob.product_id == identifier, ArticleImageJob.status == "queued"
            )
        )
        assert next_job.image_url == replacement


@pytest.mark.integration
def test_product_url_edit_and_logo_outbox_rollback_together(database, logo_catalog):
    ids, _, _ = logo_catalog
    identifier = ids["free_product"]
    with database() as session:
        original_url = session.get(PartnerProduct, identifier).product_url
    with pytest.raises(RuntimeError, match="rollback"), database.begin() as session:
        session.get(PartnerProduct, identifier).product_url = "https://rolled-back.example/tool"
        assert request_logo(session, PartnerProduct, identifier) is not None
        raise RuntimeError("rollback")
    with database() as session:
        assert session.get(PartnerProduct, identifier).product_url == original_url
        assert session.scalar(select(func.count()).select_from(ArticleImageJob)) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["pending", "excluded", "stale", "withdrawn_listing", "disabled_connection", "merged"]
)
def test_logo_backfill_only_queues_current_approved_active_products(database, logo_catalog, change):
    from devfeed_core.models import PartnerConnection

    ids, _, _ = logo_catalog
    with database.begin() as session:
        for name in ("paid_product", "unrelated"):
            session.get(PartnerProduct, ids[name]).excluded = True
        product = session.get(PartnerProduct, ids["free_product"])
        if change == "pending":
            product.status = "pending"
        elif change == "excluded":
            product.excluded = True
        elif change == "stale":
            product.verified_at = utcnow() - timedelta(days=91)
        elif change == "withdrawn_listing":
            session.scalar(
                select(PartnerListing).where(PartnerListing.product_id == product.id)
            ).active = False
        elif change == "disabled_connection":
            session.get(PartnerConnection, "free").enabled = False
        else:
            product.merged_into_id = ids["paid_product"]
    with database.begin() as session:
        assert backfill_logos(session, PartnerProduct, limit=10) == []


@pytest.mark.integration
def test_product_backfill_is_bounded_and_idempotent(database, logo_catalog):
    with database.begin() as session:
        first = backfill_logos(session, PartnerProduct, limit=1)
        assert len(first) == 1
        first_id = first[0].product_id
    with database.begin() as session:
        remaining = backfill_logos(session, PartnerProduct, limit=10)
        assert len(remaining) == 2 and all(job.product_id != first_id for job in remaining)
    with database.begin() as session:
        assert backfill_logos(session, PartnerProduct, limit=10) == []


@pytest.mark.integration
def test_product_logo_migration_live_upgrade_downgrade_preserves_legacy_jobs(database):
    migration = importlib.import_module("migrations.versions.0029_partner_product_logos")
    with database().get_bind().connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("CREATE SCHEMA product_logo_migration_fixture"))
            connection.execute(text("SET LOCAL search_path TO product_logo_migration_fixture"))
            connection.execute(
                text("CREATE TABLE partner_products (id uuid PRIMARY KEY, name text NOT NULL)")
            )
            connection.execute(
                text(
                    "CREATE TABLE article_image_jobs (id uuid PRIMARY KEY, "
                    "article_id uuid, topic_id uuid, source_id uuid, "
                    "operation text NOT NULL, status text NOT NULL, "
                    "CONSTRAINT ck_image_job_subject CHECK (" + migration.OLD_SUBJECT + "))"
                )
            )
            product_id = uuid.uuid4()
            legacy_ids = [uuid.uuid4() for _ in range(3)]
            connection.execute(
                text("INSERT INTO partner_products VALUES (:id,'Preserved product')"),
                {"id": product_id},
            )
            for identifier, column, operation in zip(
                legacy_ids,
                ("article_id", "topic_id", "source_id"),
                ("discover", "topic-logo", "source-logo"),
                strict=True,
            ):
                connection.execute(
                    text(
                        f"INSERT INTO article_image_jobs(id,{column},operation,status) "
                        "VALUES(:id,:subject,:operation,'queued')"
                    ),
                    {"id": identifier, "subject": uuid.uuid4(), "operation": operation},
                )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.scalar(text("SELECT managed_logo FROM partner_products")) == {}
                connection.execute(
                    text(
                        "INSERT INTO article_image_jobs(id,product_id,operation,status) "
                        "VALUES(:id,:product,'product-logo','queued')"
                    ),
                    {"id": uuid.uuid4(), "product": product_id},
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO article_image_jobs(id,product_id,operation,status) "
                            "VALUES(:id,:product,'product-logo','running')"
                        ),
                        {"id": uuid.uuid4(), "product": product_id},
                    )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO article_image_jobs"
                            "(id,source_id,product_id,operation,status) "
                            "VALUES(:id,:source,:product,'product-logo','succeeded')"
                        ),
                        {"id": uuid.uuid4(), "source": uuid.uuid4(), "product": product_id},
                    )
                migration.downgrade()
                assert set(connection.scalars(text("SELECT id FROM article_image_jobs"))) == set(
                    legacy_ids
                )
                assert (
                    connection.scalar(text("SELECT name FROM partner_products"))
                    == "Preserved product"
                )
                assert "managed_logo" not in {
                    column["name"] for column in inspect(connection).get_columns("partner_products")
                }
                migration.upgrade()
                assert connection.scalar(text("SELECT managed_logo FROM partner_products")) == {}
        finally:
            transaction.rollback()

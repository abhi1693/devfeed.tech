"""Deterministic native test data refuses non-disposable or remote targets."""

import importlib.util

import pytest
from devfeed_core.models import ArticleAnalysisJob, ArticleContent, ArticleEnrichmentJob, Tag
from sqlalchemy import func, select

from tests.support.paths import REPO_ROOT

SPEC = importlib.util.spec_from_file_location(
    "scale_fixture", REPO_ROOT / "scripts/scale_fixture.py"
)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


@pytest.mark.parametrize(
    ("database", "redis"),
    [
        ("postgresql+psycopg://user@database.example/devfeed_test", "redis://localhost/15"),
        ("postgresql+psycopg://user@localhost/devfeed", "redis://localhost/15"),
        ("sqlite:///devfeed_test", "redis://localhost/15"),
        ("postgresql+psycopg://user@localhost/devfeed_test", "redis://cache.example/15"),
        ("postgresql+psycopg://user@localhost/devfeed_test", "redis://localhost/0"),
        ("postgresql+psycopg://user@localhost/devfeed_test", "https://localhost/15"),
        (
            "postgresql+psycopg://user@localhost/devfeed_test?host=database.example",
            "redis://localhost/15",
        ),
        (
            "postgresql+psycopg://user@localhost/devfeed_test?dbname=production",
            "redis://localhost/15",
        ),
        (
            "postgresql+psycopg://user@localhost/devfeed_test?service=production",
            "redis://localhost/15",
        ),
        ("postgresql+psycopg://user@localhost/devfeed_test", "redis://localhost/15?db=0"),
    ],
)
def test_profile_refuses_non_disposable_or_remote_targets(database, redis):
    with pytest.raises(ValueError):
        profile.validate_targets(database, redis)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_profile_refuses_libpq_environment_routing(name):
    with pytest.raises(ValueError):
        profile.validate_targets(
            "postgresql+psycopg://user@localhost/devfeed_test",
            "redis://localhost/15",
            environment={name: "remote"},
        )
    assert profile.ISOLATED_ENV["DEVFEED_REDIS_SENTINEL_NODES"] == "[]"


def test_profile_configuration_disables_inherited_sentinel_routing(monkeypatch):
    from devfeed_core.config import Settings
    from devfeed_core.redis import create_redis

    monkeypatch.setenv("DEVFEED_REDIS_SENTINEL_NODES", '[["remote.example",26379]]')
    monkeypatch.setenv("DEVFEED_REDIS_SENTINEL_MASTER", "production")
    for name, value in profile.ISOLATED_ENV.items():
        monkeypatch.setenv(name, value)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user@localhost/devfeed_test",
        redis_url="redis://127.0.0.1:56391/15",
    )
    client = create_redis(settings)
    try:
        assert settings.redis_sentinel_nodes == []
        assert client.connection_pool.connection_kwargs["host"] == "127.0.0.1"
        assert client.connection_pool.connection_kwargs["db"] == 15
    finally:
        client.close()


def test_full_scale_exceeds_recorded_production_population():
    counts = profile.fixture_counts(1)
    assert counts["articles"] // 5 >= 30000
    for table, baseline in profile.PRODUCTION_BASELINE.items():
        if table != "active_articles":
            assert counts[table] >= baseline


@pytest.mark.parametrize(
    ("kind", "number", "expected"),
    [
        ("articles", 1, "0c73a9e2-4fbf-27b4-03ef-bea18823f98b"),
        ("sources", 2, "eb76581b-b67b-4691-0b09-504f71494064"),
        ("unicode-☕", 5001, "48c9ee49-fc05-3405-9fb5-ca201dfb6d5b"),
    ],
)
def test_fixture_ids_use_stable_sha256_prefix(kind, number, expected):
    assert str(profile.fixture_id(kind, number)) == expected


@pytest.mark.integration
@pytest.mark.parametrize("excluded_every", [40, 5])
def test_seed_rows_crosses_chunk_boundary_without_duplicate_or_skipped_ids(
    database, excluded_every
):
    count = 5001
    engine = database.kw["bind"]
    with engine.begin() as connection:
        for namespace, where in (
            ("plain-chunk", None),
            ("filtered-chunk", f"n % {excluded_every} <> 0"),
        ):
            profile.seed_rows(
                connection,
                Tag,
                count,
                {"name": "'Chunk tag '||n", "slug": f"'{namespace}-'||n"},
                namespace=namespace,
                where=where,
            )
    with database() as session:
        actual = set(session.scalars(select(Tag.id)))
    plain = {profile.fixture_id("plain-chunk", number) for number in range(1, count + 1)}
    filtered_numbers = [number for number in range(1, count * 2 + 1) if number % excluded_every][
        :count
    ]
    filtered = {profile.fixture_id("filtered-chunk", number) for number in filtered_numbers}
    assert len(plain) == len(filtered) == count
    assert actual == plain | filtered
    assert filtered_numbers[-1] > 5000


@pytest.mark.integration
def test_fixture_builds_business_job_history_without_retired_projections(database):
    counts = profile.fixture_counts(0.001)
    counts["article_enrichment_jobs"] = counts["articles"] * 2
    profile.seed_fixture(database.kw["bind"], counts, history_bytes=256)
    with database() as session:
        assert (
            session.scalar(select(func.count()).select_from(ArticleAnalysisJob))
            == counts["article_analysis_jobs"]
        )
        assert (
            session.scalar(select(func.count()).select_from(ArticleEnrichmentJob))
            == counts["article_enrichment_jobs"]
        )
        assert session.scalar(select(func.min(func.length(ArticleContent.text)))) > 100
    assert "projections" not in profile.dataset_counts(database)


@pytest.mark.parametrize(
    "origin,database,message",
    [
        ("http://analytics.example:8123", "analytics_test", "local ClickHouse"),
        ("http://user:password@127.0.0.1:8123", "analytics_test", "local ClickHouse"),
        ("http://127.0.0.1:8123/?query=DROP", "analytics_test", "local ClickHouse"),
        ("http://127.0.0.1:8123", "production", "ending in _test"),
        ("http://127.0.0.1:8123", "a;drop_test", "ending in _test"),
    ],
)
def test_profiler_refuses_nonlocal_or_unowned_analytics_targets(origin, database, message):
    with pytest.raises(ValueError, match=message):
        profile.analytics_environment(
            {
                "DEVFEED_TEST_CLICKHOUSE_URL": origin,
                "DEVFEED_TEST_CLICKHOUSE_DATABASE": database,
            }
        )


def test_profiler_analytics_settings_use_only_explicit_test_credentials():
    values = profile.analytics_environment(
        {
            "DEVFEED_TEST_CLICKHOUSE_URL": "http://127.0.0.1:8123/",
            "DEVFEED_TEST_CLICKHOUSE_DATABASE": "analytics_test",
            "DEVFEED_TEST_CLICKHOUSE_USERNAME": "fixture",
            "DEVFEED_TEST_CLICKHOUSE_PASSWORD": "fixture-only-secret",
            "DEVFEED_CLICKHOUSE_PASSWORD": "must-not-use-live-credentials",
        }
    )
    assert values == {
        "DEVFEED_CLICKHOUSE_URL": "http://127.0.0.1:8123",
        "DEVFEED_CLICKHOUSE_DATABASE": "analytics_test",
        "DEVFEED_CLICKHOUSE_USERNAME": "fixture",
        "DEVFEED_CLICKHOUSE_PASSWORD": "fixture-only-secret",
    }


def test_repeated_analytics_reset_preserves_version_history_and_escapes_data_tables(monkeypatch):
    from devfeed_core import analytics, analytics_worker
    from devfeed_core.analytics_migrations import (
        HISTORY_TABLE,
        ClickHouseMigrationRunner,
        load_migrations,
    )

    migrations = load_migrations()

    class Client:
        def __init__(self):
            self.rows = []
            self.statements = []
            self.history_exists = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            await ClickHouseMigrationRunner(self).upgrade()

        async def execute(self, statement):
            self.statements.append(statement)
            if "CREATE TABLE IF NOT EXISTS " + HISTORY_TABLE in statement:
                self.history_exists = True
            if statement == f"TRUNCATE TABLE `{HISTORY_TABLE}`":
                self.rows.clear()

        async def insert(self, table, rows):
            assert table == HISTORY_TABLE
            self.rows.extend(rows)

        async def query(self, statement):
            if statement.startswith("EXISTS TABLE"):
                return [{"result": int(self.history_exists)}]
            if statement.startswith("SELECT version,name,checksum"):
                return list(self.rows)
            if "system.tables" in statement:
                return [{"name": name} for name in (HISTORY_TABLE, "analytics_events", "odd`name")]
            return [{"count": 0}]

        async def status(self):
            return {"ready": True}

    class Worker:
        def __init__(self, client, settings):
            self.client = client

        async def once(self):
            await ClickHouseMigrationRunner(self.client).check()

    client = Client()
    monkeypatch.setattr(analytics, "AnalyticsClient", lambda settings: client)
    monkeypatch.setattr(analytics_worker, "AnalyticsImportWorker", Worker)
    monkeypatch.setattr(
        profile,
        "analytics_environment",
        lambda: {
            "DEVFEED_CLICKHOUSE_URL": "http://127.0.0.1:8123",
            "DEVFEED_CLICKHOUSE_DATABASE": "fixture_test",
            "DEVFEED_CLICKHOUSE_USERNAME": "fixture",
            "DEVFEED_CLICKHOUSE_PASSWORD": "fixture",
        },
    )
    for _ in range(2):
        report = profile.publish_analytics(object(), reset=True)
        assert report["current_cards_ready"] is True
        assert report["counts"] == {"inference_calls": 0, "partner_tracked_daily_metrics": 0}
        assert [row["version"] for row in client.rows] == [item.version for item in migrations]
    assert [sql for sql in client.statements if sql.startswith("TRUNCATE")] == [
        "TRUNCATE TABLE `analytics_events`",
        "TRUNCATE TABLE `odd``name`",
    ] * 2
    assert len(client.statements) == sum(len(item.statements) for item in migrations) + 4


@pytest.mark.integration
def test_repeated_native_analytics_reset_reseeds_data_without_erasing_schema_history(
    database, monkeypatch
):
    import asyncio
    import uuid
    from datetime import UTC, datetime

    from devfeed_core import analytics
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.analytics_inference_store import InferenceStore
    from devfeed_core.analytics_migrations import ClickHouseMigrationRunner, load_migrations

    from tests.support.analytics import settings as analytics_settings

    configured = analytics_settings()
    target = f"scale_reset_{uuid.uuid4().hex}_test"
    counts = profile.fixture_counts(0.001)
    counts["inference_calls"] = 40
    profile.seed_fixture(database.kw["bind"], counts, history_bytes=256)
    monkeypatch.setenv("DEVFEED_TEST_CLICKHOUSE_DATABASE", target)
    settings = configured.model_copy(update={"clickhouse_database": target})

    def fixture_client(settings):
        client = AnalyticsClient(settings)
        client.insert_settings = {"async_insert_busy_timeout_ms": 1}
        return client

    monkeypatch.setattr(analytics, "AnalyticsClient", fixture_client)

    async def create_or_drop(action):
        async with AnalyticsClient(
            configured.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"{action} DATABASE `{target}`")

    async def add_obsolete_data():
        async with AnalyticsClient(settings) as client:
            client.insert_settings = {"async_insert_busy_timeout_ms": 1}
            await InferenceStore(client).write(
                [{"id": uuid.uuid4(), "started_at": datetime.now(UTC), "status": "returned"}]
            )
            assert await client.query(
                "SELECT count() AS count FROM analytics_inference_events"
            ) == [{"count": counts["inference_calls"] + 1}]

    async def check_history():
        async with AnalyticsClient(settings) as client:
            assert (await ClickHouseMigrationRunner(client).check()).current_version == len(
                load_migrations()
            )
            assert await client.query(
                "SELECT count() AS count FROM analytics_schema_migrations"
            ) == [{"count": len(load_migrations())}]

    asyncio.run(create_or_drop("CREATE"))
    try:
        for attempt in range(2):
            report = profile.publish_analytics(database, reset=True, counts=counts)
            assert report["current_cards_ready"] is True
            assert report["counts"] == {
                "inference_calls": counts["inference_calls"],
                "partner_tracked_daily_metrics": counts["partner_tracked_daily_metrics"],
            }
            asyncio.run(check_history())
            if attempt == 0:
                asyncio.run(add_obsolete_data())
    finally:
        asyncio.run(create_or_drop("DROP"))

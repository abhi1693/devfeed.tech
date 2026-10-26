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

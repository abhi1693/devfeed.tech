"""The scale fixture refuses production targets and builds real durable projections."""

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleContent,
    PipelineProjection,
    ReadModelEvent,
    Tag,
)
from sqlalchemy import func, select

SPEC = importlib.util.spec_from_file_location(
    "profile_read_models", Path(__file__).parents[1] / "scripts/profile_read_models.py"
)
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
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
    assert profile.percentile([4, 1, 3, 2], 0.95) == 4


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


def test_profile_gates_reject_slow_unbounded_or_failed_reads():
    success = {"status_counts": {200: 50}, "p95_ms": 999, "queries_max": 13}
    assert profile.profile_gates({"normal": {"article-cards": success}}, p95_budget_ms=1000)[
        "passed"
    ]
    failed = dict(success, status_counts={200: 49, 500: 1}, p95_ms=1001, queries_max=100)
    gates = profile.profile_gates({"normal": {"article-cards": failed}}, p95_budget_ms=1000)
    assert not gates["passed"]
    assert gates["failures"] == [
        {
            "phase": "normal",
            "route": "article-cards",
            "reasons": ["HTTP errors", "p95 latency", "SQL query count"],
        }
    ]
    routes = {route for route, _, _ in profile.workload()}
    assert {
        "article-search",
        "article-source",
        "article-attention",
        "article-deep-column",
    } <= routes
    assert {"source-search", "source-type", "source-attention", "overview-main"} <= routes
    for invalid in (float("nan"), float("inf"), -1, 0):
        with pytest.raises(ValueError):
            profile.profile_gates({}, p95_budget_ms=invalid)
    assert (
        profile.dataset_qualification({}, profile.fixture_counts(1))["label"] == "functional smoke"
    )
    complete = dict(profile.COUNTS, active_articles=30000)
    assert (
        profile.dataset_qualification(complete, complete)["label"] == "production-scale synthetic"
    )


@pytest.mark.integration
def test_seed_rows_crosses_chunk_boundary_without_duplicate_or_skipped_ids(database):
    count = 5001
    engine = database.kw["bind"]
    with engine.begin() as connection:
        for namespace, where in (("plain-chunk", None), ("filtered-chunk", "n % 40 <> 0")):
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
    filtered_numbers = [number for number in range(1, 5300) if number % 40][:count]
    filtered = {profile.fixture_id("filtered-chunk", number) for number in filtered_numbers}
    assert len(plain) == len(filtered) == count
    assert actual == plain | filtered
    assert filtered_numbers[-1] > 5000


@pytest.mark.integration
def test_fixture_builds_real_jobs_history_and_projection_backfill(database):
    from devfeed_admin_api.pipeline_projection import drain_pipeline

    counts = profile.fixture_counts(0.001)
    engine = database.kw["bind"]
    profile.seed_fixture(engine, counts, history_bytes=256)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PipelineProjection)) == 0
        assert (
            session.scalar(select(func.count()).select_from(ArticleAnalysisJob))
            == counts["article_analysis_jobs"]
        )
        assert session.scalar(select(func.min(func.length(ArticleContent.text)))) > 100
        assert [
            (event.kind, event.entity_id) for event in session.scalars(select(ReadModelEvent))
        ] == [("policy", None)]
    drain_pipeline(database, batch=30)
    result = profile.parity(database)
    assert result["matches_legacy_lifecycle"]
    assert len(result["checked"]) >= 5


@pytest.mark.integration
def test_profile_authentication_uses_real_revocable_redis_sessions(database, monkeypatch):
    from devfeed_admin_api.auth import require_admin
    from devfeed_admin_api.config import get_settings as admin_settings
    from devfeed_partner_api.auth import require_partner
    from devfeed_partner_api.config import get_settings as partner_settings

    for key, value in profile.AUTH_ENV.items():
        monkeypatch.setenv(key, value)
    admin_settings.cache_clear()
    partner_settings.cache_clear()
    applications = profile.apps(database)
    assert require_admin not in applications[0].dependency_overrides
    assert require_partner not in applications[1].dependency_overrides

    async def verify():
        for app, service in zip(applications, ("admin", "partner"), strict=True):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://profile.test",
                cookies=app.state.profile_cookies,
            ) as client:
                assert (await client.get(f"/v1/{service}/auth/me")).status_code == 200
                redis, key = app.state.profile_session
                redis.delete(key)
                assert (await client.get(f"/v1/{service}/auth/me")).status_code == 401

    try:
        asyncio.run(verify())
    finally:
        for app in applications:
            redis, key = app.state.profile_session
            redis.delete(key)
        admin_settings.cache_clear()
        partner_settings.cache_clear()

"""The scale fixture refuses production targets and builds real durable projections."""

import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    Tag,
    utcnow,
)
from sqlalchemy import func, select

from tests.support.paths import REPO_ROOT

SPEC = importlib.util.spec_from_file_location(
    "profile_read_models", REPO_ROOT / "scripts/profile_read_models.py"
)
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
SPEC.loader.exec_module(profile)

sys.path.insert(0, str(REPO_ROOT / "scripts"))
try:
    import profile_scheduler_rechecks as scheduler_profile
finally:
    sys.path.pop(0)


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


def test_scheduler_profile_100k_history_and_backlog_exceed_fresh_production():
    args = SimpleNamespace(scale=2 / 3, ticks=5, batch=50, ingest_per_tick=10, history_bytes=8192)
    counts = scheduler_profile.validate_arguments(args)
    assert counts["articles"] == 100000
    for name, production in scheduler_profile.PRODUCTION_2026_10_24.items():
        if name not in {"queued_analysis", "running_analysis"}:
            assert counts[name] >= production, name
    for field, value in (
        ("ticks", 0),
        ("batch", 51),
        ("ingest_per_tick", 0),
        ("history_bytes", 127),
        ("scale", 0.001),
    ):
        with pytest.raises(ValueError):
            scheduler_profile.validate_arguments(SimpleNamespace(**(vars(args) | {field: value})))


def test_scheduler_profile_rejects_card_amplification_and_missing_ingestion():
    tick = {
        "tick": 1,
        "outcome": {"articles_checked": 50, "articles_published": 0, "articles_rejected": 0},
        "cohort_events": 0,
        "hydration": {"cohort_ids": 0},
        "ingested": 10,
        "ingested_projected": 10,
        "ingestion_overlapped_scheduler": True,
        "active_analysis_unchanged": True,
        "cohort_clock_visible": True,
        "cohort_retry_transition_correct": True,
    }
    assert scheduler_profile.bounded_failures([tick], batch=50, ingest_per_tick=10) == []
    broken = tick | {
        "cohort_events": 50,
        "hydration": {"cohort_ids": 50},
        "ingested_projected": 0,
        "active_analysis_unchanged": False,
    }
    failures = scheduler_profile.bounded_failures([broken], batch=50, ingest_per_tick=10)
    assert failures == [
        {
            "tick": 1,
            "reasons": [
                "clock-only rechecks generated card events or hydration",
                "genuine concurrent ingestion was not projected",
                "active analysis lease, retry, or identity changed",
            ],
        }
    ]
    assert scheduler_profile.bounded_failures(
        [tick | {"publisher_deleted_rows": {"publisher": 200}}], batch=50, ingest_per_tick=10
    ) == [{"tick": 1, "reasons": ["unrelated retention cleanup entered the measured workload"]}]


@pytest.mark.integration
def test_scheduler_fixture_reuse_preserves_history_and_refuses_unrecognized_corpus(database):
    from devfeed_core.automation_demands import POLICY_STATE
    from devfeed_core.read_models import set_state

    now = utcnow()
    identifier = profile.fixture_id("articles", 1)
    with database.begin() as session:
        session.add_all(
            [
                Article(
                    id=identifier,
                    canonical_url="https://publisher.example/articles/1",
                    url_hash="historical",
                    title="Historical fixture",
                ),
                Article(
                    canonical_url="https://publisher.example/scheduler-profile/0/1",
                    url_hash="measured",
                    title="Measured ingestion",
                ),
            ]
        )
        session.flush()
        session.add(ArticleAnalysisJob(article_id=identifier, input_snapshot={"retained": True}))
        set_state(session, POLICY_STATE, {"policy": "observed setup"})
    scheduler_profile.prepare_projections(database, [identifier], now)
    with database() as session:
        assert session.get(ReadModelState, POLICY_STATE).payload == {"policy": "observed setup"}
    with pytest.raises(ValueError, match="matching historical corpus"):
        scheduler_profile.reset_workload(database, articles=2)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2
    scheduler_profile.reset_workload(database, articles=1)
    with database() as session:
        assert list(session.scalars(select(Article.id))) == [identifier]
        assert session.scalar(select(ArticleAnalysisJob)).input_snapshot == {"retained": True}
        assert session.scalar(select(func.count()).select_from(PipelineProjection)) == 0
        assert session.scalar(select(func.count()).select_from(ReadModelEvent)) == 0


@pytest.mark.integration
def test_scheduler_fixture_excludes_expired_cards_before_warmup_and_replays_due_plans(database):
    now = utcnow()
    identifier = profile.fixture_id("articles", 1)
    future = now + timedelta(hours=1)
    with database.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url="https://publisher.example/articles/1",
                url_hash="pending",
                title="Pending fixture",
                automation_next_check_at=future,
            )
        )
        for age in (1, 8):
            session.add(
                Article(
                    canonical_url=f"https://publisher.example/articles/published/{age}",
                    url_hash=f"published-{age}",
                    title="Published fixture",
                    review_status="approved",
                    publication_status="published",
                    published_to_feed_at=now - timedelta(days=age),
                )
            )
    scheduler_profile.prepare_projections(database, [identifier], now)
    engine = database.kw["bind"]
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PipelineProjection)) == 2
    recorder = scheduler_profile.Recorder(engine)
    recorder.plans["scheduler_due_candidates"] = (
        "SELECT articles.id FROM articles WHERE articles.automation_next_check_at "
        "<= %(automation_next_check_at_1)s ORDER BY articles.automation_next_check_at LIMIT 1",
        {"automation_next_check_at_1": now - timedelta(seconds=1)},
    )
    plans = recorder.explain(identifiers=[identifier], next_check=now + timedelta(hours=2))
    assert plans["scheduler_due_candidates"]["plan"][0]["Plan"]["Actual Rows"] == 1
    with database() as session:
        assert session.get(Article, identifier).automation_next_check_at == future
        payload = session.scalar(
            select(PipelineProjection.payload).where(PipelineProjection.entity_id == identifier)
        )
        assert "next_check_at" not in payload


def test_scheduler_profile_captures_due_query_and_clock_trigger_plans():
    assert (
        scheduler_profile.classify_plan(
            "SELECT articles.id FROM articles ORDER BY articles.automation_next_check_at",
            "scheduler",
        )
        == "scheduler_due_candidates"
    )
    assert (
        scheduler_profile.classify_plan(
            "UPDATE articles SET automation_next_check_at=%(value)s", "scheduler"
        )
        == "scheduler_clock_update"
    )
    assert scheduler_profile.classify_plan("SELECT 1", "scheduler") is None


@pytest.mark.integration
@pytest.mark.parametrize("busy_owner", [False, True])
def test_scheduler_publisher_uses_a_fresh_lease_after_slow_setup(database, busy_owner):
    from devfeed_admin_api.pipeline_projection import LEASE_SECONDS, project_batch

    setup_time = utcnow() - timedelta(seconds=LEASE_SECONDS + 30)
    identifier = profile.fixture_id("articles", 1)
    deadline = utcnow() + timedelta(hours=1)
    with database.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url="https://publisher.example/articles/1",
                url_hash="slow-setup",
                title="Python runtime diagnostics",
                automation_next_check_at=deadline,
            )
        )
    scheduler_profile.prepare_projections(database, [identifier], setup_time)
    if busy_owner:
        with database.begin() as session:
            session.add(
                ReadModelState(
                    name="pipeline-worker",
                    payload={
                        "token": "another-publisher",
                        "expires_at": (utcnow() + timedelta(seconds=LEASE_SECONDS)).isoformat(),
                    },
                )
            )
        with pytest.raises(RuntimeError, match="stopped with pending events"):
            scheduler_profile.drain_at(database, setup_time)
    else:
        # A lease acquired at the original setup timestamp is already expired:
        # publication correctly refuses it and leaves its event retryable.
        assert project_batch(database, now=setup_time) == 0
        assert scheduler_profile.drain_at(database, setup_time) == 1
    with database() as session:
        pending = session.scalar(select(func.count()).select_from(ReadModelEvent))
        assert pending == int(busy_owner)
        card = session.scalar(
            select(PipelineProjection).where(PipelineProjection.entity_id == identifier)
        )
        if busy_owner:
            assert card.payload == {"_profile_placeholder": True}
        else:
            assert card.payload["title"] == "Python runtime diagnostics"
            assert datetime.fromisoformat(card.payload["next_check_at"]) == deadline
            assert session.get(ReadModelState, "pipeline-worker").payload == {}


def test_scheduler_publisher_refuses_unbounded_work(monkeypatch):
    from devfeed_admin_api import pipeline_projection

    calls = 0

    def always_more_work(*args, **kwargs):
        nonlocal calls
        calls += 1
        return 1

    monkeypatch.setattr(pipeline_projection, "project_batch", always_more_work)
    with pytest.raises(RuntimeError, match="exceeded its bounded drain"):
        scheduler_profile.drain_at(None, utcnow())
    assert calls == 10000


@pytest.mark.integration
def test_scheduler_publisher_preserves_simulated_future_retry_deadlines(database):
    now = utcnow()
    identifier = profile.fixture_id("articles", 1)
    retry = now + timedelta(minutes=5)
    with database.begin() as session:
        session.add(
            Article(
                id=identifier,
                canonical_url="https://publisher.example/articles/1",
                url_hash="future-retry",
                title="Python runtime diagnostics",
            )
        )
        session.flush()
        session.add(ArticleAnalysisJob(article_id=identifier, status="queued", available_at=retry))
    scheduler_profile.prepare_projections(database, [identifier], now)
    assert scheduler_profile.drain_at(database, now) == 1
    with database() as session:
        projection = session.scalar(select(PipelineProjection))
        assert projection.next_transition_at == retry
    assert scheduler_profile.drain_at(database, now + timedelta(minutes=10)) == 1
    with database() as session:
        projection = session.scalar(select(PipelineProjection))
        assert projection.next_transition_at is None
        assert session.scalar(select(func.count()).select_from(ReadModelEvent)) == 0


@pytest.mark.integration
def test_scheduler_cli_profiles_real_seeded_and_reused_workloads(database, monkeypatch, tmp_path):
    from devfeed_core import article_automation
    from devfeed_core.config import Settings

    # Keep the CLI's intentional process-wide configuration local to this test.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(Settings, "model_config", Settings.model_config.copy())
    # Force setup fanout to span many pages, as on the native100k/150k corpus.
    monkeypatch.setenv("DEVFEED_AUTOMATION_INVALIDATION_BATCH_SIZE", "10")
    prepared = []
    prepare = article_automation._prepare_article_candidates

    def candidates(factory, identifier):
        prepared.append(identifier)
        return prepare(factory, identifier)

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", candidates)
    # Ordinary integration servers do not preload pg_stat_statements. The native
    # scale workflow covers that sampler; exercise the real workload, SQL recorder
    # and rollback-only plans here without requiring a server restart.
    monkeypatch.setattr(scheduler_profile, "statement_totals", lambda engine: {"calls": 0})
    reports = []
    for mode in ("--seed", "--reuse-fixture"):
        prepared.clear()
        output = tmp_path / (mode.removeprefix("--") + ".json")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "profile_scheduler_rechecks.py",
                mode,
                "--scale",
                "0.0014",
                "--ticks",
                "2",
                "--batch",
                "5",
                "--ingest-per-tick",
                "2",
                "--history-bytes",
                "256",
                "--assert-bounded",
                "--output",
                str(output),
            ],
        )
        scheduler_profile.main()
        result = json.loads(output.read_text())
        assert set(prepared) == {profile.fixture_id("articles", 20 + 40 * i) for i in range(5)}
        assert len(prepared) == 15  # Five warmup candidates and two measured batches.
        assert result["complete"] and result["checkpoint_before_workload"]
        assert result["bounded_failures"] == []
        assert result["dataset"]["articles"] == 210
        assert result["automation_setup"]["expanded"] == result["dataset"]["active_articles"]
        assert result["automation_setup"]["seconds"] > 0
        assert len(result["ticks"]) == 2
        for tick in result["ticks"]:
            assert tick["outcome"]["articles_checked"] == 5
            assert tick["ingested"] == tick["ingested_projected"] == 2
            assert tick["active_analysis_unchanged"]
            assert tick["cohort_clock_visible"] and tick["cohort_retry_transition_correct"]
            assert tick["hydration"]["article_ids"] == 2
            assert all(
                tick["sql_statements"][name] > 0 for name in ("scheduler", "publisher", "ingestion")
            )
        due = result["plans"]["scheduler_due_candidates"]["plan"][0]["Plan"]
        assert due["Actual Rows"] == 5
        assert result["postgres_workload_delta"] == {"calls": 0}
        assert result["workload_seconds"] > 0
        assert result["application_cpu_seconds"] > 0
        reports.append(result)
    assert reports[0]["dataset"] == reports[1]["dataset"]
    assert reports[1]["fixture_reused"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--seed", "--reuse-fixture"],
        ["--scale", "0"],
        ["--ticks", "21"],
    ],
)
def test_scheduler_cli_rejects_unsafe_work_before_connecting(monkeypatch, tmp_path, arguments):
    monkeypatch.setenv(
        "DEVFEED_TEST_DATABASE_URL", "postgresql+psycopg://profile@localhost/fixture_test"
    )
    monkeypatch.setenv("DEVFEED_TEST_REDIS_URL", "redis://localhost/15")
    output = tmp_path / "refused.json"
    monkeypatch.setattr(sys, "argv", ["profile", "--output", str(output), *arguments])
    with pytest.raises(SystemExit) as error:
        scheduler_profile.main()
    assert error.value.code == 2
    assert not output.exists()


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

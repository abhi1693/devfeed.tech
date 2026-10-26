"""Catalog scale evidence accepts complete expected outcomes and rejects partial runs."""

import copy
import importlib.util
import json
import os
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import column, delete, select, table
from sqlalchemy.dialects import postgresql

from tests.support.paths import REPO_ROOT

if "profile_read_models" not in sys.modules:
    HELPER_SPEC = importlib.util.spec_from_file_location(
        "profile_read_models", REPO_ROOT / "scripts/profile_read_models.py"
    )
    helper = importlib.util.module_from_spec(HELPER_SPEC)
    sys.modules[HELPER_SPEC.name] = helper
    HELPER_SPEC.loader.exec_module(helper)

SPEC = importlib.util.spec_from_file_location(
    "profile_catalog_refresh", REPO_ROOT / "scripts/profile_catalog_refresh.py"
)
profile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profile
SPEC.loader.exec_module(profile)


def report(group, generation=10):
    return {
        "group": group,
        "published": True,
        "snapshot": {
            "status": "catching_up",
            "policy_version": "current-policy",
            "generation": f"current-policy:{generation}",
            "article_generation": generation,
            "lag_seconds": 2,
            "catalog_lag_seconds": 61,
        },
    }


@pytest.mark.parametrize("outcome", ["published", "declined", "empty", "failed"])
def test_catalog_measurement_publishes_only_operations_without_clickhouse(monkeypatch, outcome):
    from devfeed_admin_api import overview_projection as reports

    factory = SimpleNamespace(begin=lambda: nullcontext(object()))
    from devfeed_admin_api.overview_windows import current_window

    claim = ("operations", current_window(), "fixture-token", 0)
    request = Mock()
    failed = Mock()
    calculate = Mock(
        return_value=SimpleNamespace(snapshot=None),
        side_effect=RuntimeError("fixture calculation failed") if outcome == "failed" else None,
    )
    publish = Mock(return_value=outcome == "published")
    monkeypatch.setattr(reports, "request_refresh", request)
    monkeypatch.setattr(
        reports, "claim_refresh", lambda *_args: None if outcome == "empty" else claim
    )
    monkeypatch.setattr(reports, "calculate_report", calculate)
    monkeypatch.setattr(reports, "publish_report", publish)
    monkeypatch.setattr(reports, "fail_refresh", failed)
    monkeypatch.setattr(
        "devfeed_core.analytics.AnalyticsClient",
        Mock(side_effect=AssertionError("Catalog benchmark connected to ClickHouse")),
    )
    result = profile.publish(factory)
    assert request.call_args.args[1:3] == ("operations", current_window())
    if outcome == "empty":
        assert result == []
        calculate.assert_not_called()
    else:
        assert result[0]["group"] == "operations"
        assert calculate.call_args.args[1:3] == ("operations", current_window())
        if outcome == "failed":
            assert result[0]["error"] == "RuntimeError"
            failed.assert_called_once()
            publish.assert_not_called()
        else:
            assert result[0]["published"] == (outcome == "published")
            failed.assert_not_called()


def evidence(behavior="fixed"):
    samples = [
        {
            "reports": {"result": [report("operations", 10 + index)]},
            "state": {"catalog_cursors": [f"cursor-{index}"]},
        }
        for index in range(2)
    ]
    if behavior == "baseline":
        samples[0]["reports"]["result"] = [
            {"group": group, "published": False, "error": "ProjectionNotReady"}
            for group in ("operations",)
        ]
        # Failure cooldowns intentionally prevent immediate additional claims.
        samples[1]["reports"]["result"] = []
    return {
        "continuous": samples,
        "stable_reports": {"result": [report("operations")]},
        "concurrent_ingestion": {"commits": 2},
        "final_state": {"pending_events": 0, "pipeline": {}},
        "legacy_lifecycle_parity": {"matches_legacy_lifecycle": True},
        "workload": {"require_native_arm64": False},
        "qualification": {"native_arm64_postgresql_17": False},
    }


@pytest.mark.parametrize("behavior", ["baseline", "fixed"])
def test_either_accepts_complete_baseline_or_fixed_evidence(behavior):
    output = evidence(behavior)
    for expected in (behavior, "either"):
        gates = profile.gates(output, expected)
        assert gates["passed"] and gates["observed_behavior"] == behavior
    opposite = "baseline" if behavior == "fixed" else "fixed"
    assert not profile.gates(output, opposite)["passed"]


@pytest.mark.parametrize("failure", ["unexpected-error", "missing-group", "partial-publication"])
@pytest.mark.parametrize("expected", ["baseline", "either"])
def test_baseline_cannot_hide_partial_publication_or_an_unrelated_failure(failure, expected):
    output = evidence("baseline")
    results = output["continuous"][0]["reports"]["result"]
    if failure == "unexpected-error":
        results[0]["error"] = "TimeoutError"
    elif failure == "missing-group":
        results.pop()
    else:
        results[0] = report("operations")
    assert not profile.gates(output, expected)["passed"]


@pytest.mark.parametrize("failure", ["missing-report", "duplicate-group", "empty-batch"])
@pytest.mark.parametrize("expected", ["fixed", "either"])
def test_fixed_requires_requested_operational_report_in_every_batch(failure, expected):
    output = evidence()
    results = output["continuous"][1]["reports"]["result"]
    if failure == "missing-report":
        results.pop()
    elif failure == "duplicate-group":
        results.append(report("operations"))
    else:
        results.clear()
    assert not profile.gates(output, expected)["passed"]


@pytest.mark.parametrize("failure", ["stable-report", "writer", "backlog", "cursor", "parity"])
def test_behavioral_success_cannot_hide_failed_setup_or_recovery(failure):
    output = evidence()
    if failure == "stable-report":
        output["stable_reports"]["result"][0]["group"] = "coverage"
    elif failure == "writer":
        output["concurrent_ingestion"]["error"] = "Writer failed"
    elif failure == "backlog":
        output["final_state"]["pending_events"] = 1
    elif failure == "cursor":
        output["continuous"][1]["state"]["catalog_cursors"] = ["cursor-0"]
    else:
        output["legacy_lifecycle_parity"]["matches_legacy_lifecycle"] = False
    assert not profile.gates(output, "either")["passed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "fresh"),
        ("lag_seconds", 61),
        ("lag_seconds", None),
        ("lag_seconds", True),
        ("lag_seconds", -1),
        ("lag_seconds", float("nan")),
        ("lag_seconds", float("inf")),
        ("catalog_lag_seconds", 59),
        ("catalog_lag_seconds", None),
        ("catalog_lag_seconds", True),
        ("catalog_lag_seconds", float("nan")),
        ("article_generation", None),
        ("article_generation", True),
        ("article_generation", -1),
    ],
)
def test_fixed_requires_explicit_generation_and_honest_separate_freshness(field, value):
    output = evidence()
    output["continuous"][0]["reports"]["result"][0]["snapshot"][field] = value
    assert not profile.gates(output, "either")["passed"]


def test_fixed_generations_cannot_move_backwards():
    output = evidence()
    output["continuous"][1]["reports"]["result"] = [report("operations", 9)]
    gates = profile.gates(output, "fixed")
    assert not gates["passed"] and not gates["checks"]["publication_generations_do_not_regress"]


def test_native_requirement_is_an_enforced_gate_independent_of_behavior():
    output = evidence()
    output["workload"]["require_native_arm64"] = True
    assert not profile.gates(output, "fixed")["passed"]
    qualified = copy.deepcopy(output)
    qualified["qualification"]["native_arm64_postgresql_17"] = True
    assert profile.gates(qualified, "fixed")["passed"]


def test_sql_evidence_captures_cte_selects_without_explaining_writes():
    cards = table("pipeline_projections", column("entity_id"))
    targets = select(cards.c.entity_id).cte("blocker_targets")
    compiled = select(targets.c.entity_id).compile(dialect=postgresql.dialect())
    query = str(compiled)
    assert query.startswith("WITH ")
    samples = profile.SQLSamples(None)

    def observe(statement):
        sql = str(statement)
        context = SimpleNamespace(compiled=statement)
        samples.before(None, None, sql, statement.params, context, False)
        samples.after(None, None, sql, statement.params, context, False)

    observe(compiled)
    observe(compiled)
    for write in (delete(cards), delete(cards).add_cte(targets)):
        observe(write.compile(dialect=postgresql.dialect()))

    assert set(samples.queries) == {query}
    captured = samples.queries[query]
    assert captured["calls"] == 2 and captured["parameters"] == compiled.params
    assert captured["sql_seconds"] >= 0


@pytest.mark.parametrize(
    "database_url,redis_url,arguments,message",
    [
        (None, "redis://127.0.0.1/15", [], "DEVFEED_TEST_DATABASE_URL"),
        (
            "postgresql+psycopg://test@database.invalid/profile_test",
            "redis://127.0.0.1/15",
            [],
            "explicitly local PostgreSQL",
        ),
        (
            "postgresql+psycopg://test@127.0.0.1/profile",
            "redis://127.0.0.1/15",
            [],
            "database must end in _test",
        ),
        (
            "postgresql+psycopg://test@127.0.0.1/profile_test",
            "redis://127.0.0.1/0",
            [],
            "local Redis database 15",
        ),
        *[
            (
                "postgresql+psycopg://test@127.0.0.1/profile_test",
                "redis://127.0.0.1/15",
                arguments,
                "bounded workload limits",
            )
            for arguments in (
                ["--batch", "0"],
                ["--batches", "10001"],
                ["--age-refresh-seconds", "3601"],
                ["--history-bytes", "127"],
            )
        ],
    ],
)
def test_cli_refuses_unsafe_targets_and_unbounded_work_before_connecting(
    monkeypatch, tmp_path, capsys, database_url, redis_url, arguments, message
):
    from devfeed_core import db

    if database_url is None:
        monkeypatch.delenv("DEVFEED_TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", database_url)
    monkeypatch.setenv("DEVFEED_TEST_REDIS_URL", redis_url)
    monkeypatch.setattr(
        db, "create_database_engine", lambda *_: pytest.fail("Unsafe CLI connected to a database")
    )
    output = tmp_path / "profile.json"
    monkeypatch.setattr(
        sys, "argv", ["profile_catalog_refresh.py", "--output", str(output), *arguments]
    )
    with pytest.raises(SystemExit) as error:
        profile.main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


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
        sys.modules["profile_read_models"].analytics_environment(
            {
                "DEVFEED_TEST_CLICKHOUSE_URL": origin,
                "DEVFEED_TEST_CLICKHOUSE_DATABASE": database,
            }
        )


def test_profiler_analytics_settings_use_only_explicit_test_credentials():
    values = sys.modules["profile_read_models"].analytics_environment(
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


@pytest.mark.integration
@pytest.mark.parametrize("prepare_only", [False, True])
def test_cli_profiles_real_concurrent_publication_and_preparation(
    database, monkeypatch, tmp_path, prepare_only
):
    from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, IngestionJob
    from devfeed_core.read_models import pipeline_policy_version
    from sqlalchemy import func

    output_path = tmp_path / "catalog-smoke.json"
    arguments = [
        "profile_catalog_refresh.py",
        "--seed",
        "--scale",
        "0.0004",
        "--history-bytes",
        "128",
        "--batch",
        "6",
        "--batches",
        "2",
        "--environment-note",
        "Disposable CI smoke; does not qualify as production-volume evidence",
        "--output",
        str(output_path),
    ]
    if prepare_only:
        arguments.append("--prepare-only")
    monkeypatch.setattr(sys, "argv", arguments)
    # main owns a separate engine; preserve the fixture's disposable URLs and
    # restore all environment changes before subsequent domain tests run.
    with patch.dict(os.environ):
        assert profile.main() == 0
    output = json.loads(output_path.read_text())
    assert output["dataset"]["articles"] == 60
    # Even uninterrupted scan batches must leave work outstanding throughout
    # both samples; a tiny fixture must not accidentally test an idle catalog.
    assert output["dataset"]["retained_article_cards"] > output["workload"]["batch"] * (
        output["workload"]["batches"] + 1
    )
    assert output["prepared_retry_timers"]["queued_enrichment_jobs"] > 0
    assert output["workload"]["require_native_arm64"] is False
    assert "does not qualify" in output["hardware"]["note"]
    if prepare_only:
        assert output["prepared"] is True and output["phase"] == "prepared"
        assert output["complete"] is False
        assert "continuous" not in output
        return
    assert output["complete"] is True and output["phase"] == "complete"
    assert output["qualification"]["meets_issue_article_population"] is False
    assert output["gates"]["passed"] and output["gates"]["observed_behavior"] == "fixed"
    assert {row["group"] for row in output["stable_reports"]["result"]} == profile.REPORT_GROUPS
    assert all(row["published"] for row in output["stable_reports"]["result"])
    assert len(output["continuous"]) == 2
    generations = []
    for sample in output["continuous"]:
        results = sample["reports"]["result"]
        assert {row["group"] for row in results} == profile.REPORT_GROUPS
        assert all(row["published"] for row in results)
        assert all(row["snapshot"]["status"] == "catching_up" for row in results)
        assert all(
            row["snapshot"]["policy_version"] == pipeline_policy_version() for row in results
        )
        generations.append(min(row["snapshot"]["article_generation"] for row in results))
    assert generations == sorted(generations)
    writer = output["concurrent_ingestion"]
    assert writer["commits"] == 2 and "error" not in writer
    assert len(writer["article_ids"]) == len(writer["ingestion_job_ids"]) == 2
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 62
        for identifier in writer["article_ids"]:
            assert session.get(Article, identifier) is not None
            assert session.scalar(
                select(ArticleOrigin).where(ArticleOrigin.article_id == identifier)
            )
            assert session.scalar(
                select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == identifier)
            )
        assert all(
            session.get(IngestionJob, identifier) for identifier in writer["ingestion_job_ids"]
        )
    assert output["final_state"]["pending_events"] == 0
    assert not output["final_state"]["pipeline"].get("catalog_refresh")
    assert output["legacy_lifecycle_parity"]["matches_legacy_lifecycle"] is True
    for name in ("stable_query_plans", "query_plans"):
        assert output[name] and all(row["calls"] >= 1 and row["plan"] for row in output[name])
    assert output["server_wal_bytes"] > 0


@pytest.mark.integration
def test_cli_refuses_native_qualification_before_seeding_an_existing_fixture(
    database, monkeypatch, tmp_path, capsys
):
    from devfeed_core.models import Topic

    with database.begin() as session:
        sentinel = Topic(name="Preserve this fixture", slug="preserve-fixture", kind="technology")
        session.add(sentinel)
        session.flush()
        identifier = sentinel.id
    output = tmp_path / "native-refused.json"
    monkeypatch.setattr(profile.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        sys,
        "argv",
        ["profile_catalog_refresh.py", "--seed", "--require-native-arm64", "--output", str(output)],
    )
    with patch.dict(os.environ), pytest.raises(SystemExit) as error:
        profile.main()
    assert error.value.code == 2
    assert "Native ARM64 Python and PostgreSQL 17 are required" in capsys.readouterr().err
    assert not output.exists()
    with database() as session:
        assert session.get(Topic, identifier) is not None

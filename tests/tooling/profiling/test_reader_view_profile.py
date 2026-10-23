"""Reader scale evidence refuses unsafe targets and exercises actual authenticated APIs."""

import importlib.util
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from tests.support.paths import REPO_ROOT

ROOT = REPO_ROOT
SPEC = importlib.util.spec_from_file_location(
    "profile_reader_views", ROOT / "scripts/profile_reader_views.py"
)
assert SPEC is not None and SPEC.loader is not None
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


@pytest.mark.parametrize("scale", [0, -1, 3, float("nan"), float("inf")])
def test_reader_profile_rejects_invalid_fixture_scales(scale):
    with pytest.raises(ValueError):
        profile.fixture_counts(scale)


def test_reader_profile_distinguishes_smoke_from_above_production():
    counts = profile.fixture_counts(1)
    counts |= {
        "published_approved_articles": 400000,
        "user_topics": 99805,
        "user_sources": 99805,
        "user_tags": 99805,
    }
    assert profile.qualification(counts)["above_production"]
    assert profile.qualification(profile.fixture_counts(0.001))["label"] == "functional smoke"
    counts["article_tags"] = profile.PRODUCTION["article_tags"]
    assert not profile.qualification(counts)["above_production"]


def test_reader_profile_isolates_inherited_settings(monkeypatch):
    monkeypatch.setattr(
        os,
        "environ",
        dict(
            os.environ,
            DEVFEED_DATABASE_URL="production",
            DEVFEED_REDIS_SENTINEL_MASTER="production",
        ),
    )
    profile.isolated_environment("postgresql://localhost/reader_test", "redis://localhost/15")
    assert os.environ["DEVFEED_DATABASE_URL"] == "postgresql://localhost/reader_test"
    assert "DEVFEED_REDIS_SENTINEL_MASTER" not in os.environ
    assert os.environ["DEVFEED_AI_ENABLED"] == "false"
    assert os.environ["DEVFEED_CACHE_ENABLED"] == "false"


def test_reader_profile_gates_fail_on_errors_queries_and_latency():
    healthy = {
        "statuses": {200: 100},
        "invalid_payloads": 0,
        "queries_min": 1,
        "queries_max": 1,
        "p95_ms": 20,
    }
    assert profile.measurement_gates({"1": {"routes": {"feed-view": healthy}}}, 100)["passed"]
    cases: list[tuple[dict[str, object], str]] = [
        ({"statuses": {503: 1}}, "failed responses"),
        ({"invalid_payloads": 1}, "failed responses"),
        ({"queries_max": 2}, "SQL budget"),
        ({"queries_min": 0}, "SQL budget"),
        ({"p95_ms": 101}, "p95 latency"),
    ]
    for changes, reason in cases:
        result = profile.measurement_gates({"8": {"routes": {"feed-view": healthy | changes}}}, 100)
        assert not result["passed"]
        assert reason in result["failures"][0]["reasons"]


def test_revision_aliases_select_the_requested_checkout_in_a_fresh_process(tmp_path):
    for name in ("devfeed_core", "devfeed_api", "devfeed_user_api", "devfeed_http"):
        project = tmp_path / "packages" / name
        (project / "src").mkdir(parents=True)
        (project / "src/__init__.py").write_text("REVISION = 'selected'\n")
        (project / "pyproject.toml").write_text(f'[tool.setuptools.package-dir]\n{name} = "src"\n')
    program = """
import pathlib, runpy, sys
module = runpy.run_path(sys.argv[1])
with module['revision_imports'](pathlib.Path(sys.argv[2])) as provenance:
    assert len(provenance) == 4
    assert all(pathlib.Path(path).is_relative_to(sys.argv[2]) for path in provenance.values())
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(ROOT / "scripts/profile_reader_views.py"),
            str(tmp_path),
        ],
        check=True,
        timeout=20,
    )
    with pytest.raises(ValueError, match="fresh Python process"), profile.revision_imports(ROOT):
        pass


def test_seed_refuses_populated_tables_before_altering_triggers():
    statements = []

    class Connection:
        def execute(self, statement):
            statements.append(str(statement))

        def scalar(self, _statement):
            return 1

    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()

    with pytest.raises(ValueError, match="empty fixture tables"):
        profile.seed_fixture(Engine(), profile.fixture_counts(0.001), profile.helper_module())
    assert all("ALTER TABLE" not in statement for statement in statements)


def run_profile_cli(monkeypatch, tmp_path, *extra_args):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    report = tmp_path / "reader-profile.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "profile_reader_views.py",
            "--seed",
            "--scale",
            ".001",
            "--repeats",
            "2",
            "--concurrency",
            "1,2",
            "--p95-budget-ms",
            "60000",
            "--output",
            str(report),
        ]
        + list(extra_args),
    )

    @contextmanager
    def current_revision(_root):
        yield {"devfeed_core": str(ROOT / "packages/core/src/__init__.py")}

    # This pytest process already imported the selected checkout. The alias-selection
    # behavior itself is tested above in an independent interpreter.
    monkeypatch.setattr(profile, "revision_imports", current_revision)
    from devfeed_user_api.config import get_settings as user_settings
    from devfeed_user_api.dependencies import get_redis

    try:
        profile.main()
    finally:
        user_settings.cache_clear()
        if get_redis.cache_info().currsize:
            get_redis().close()
        get_redis.cache_clear()
    return json.loads(report.read_text())


@pytest.mark.integration
def test_reader_profile_smoke_checks_seed_plans_real_sessions_and_route_budgets(
    database, monkeypatch, tmp_path
):
    """The main workflow runs in-process so CI measures this operational source too."""
    result = run_profile_cli(monkeypatch, tmp_path)
    assert result["complete"] and result["gates"]["passed"]
    assert result["dataset"]["counts"]["articles"] == 3000
    assert result["dataset"]["counts"]["published_approved_articles"] == 2400
    clock = datetime.fromisoformat(result["dataset"]["fixture_clock_utc"])
    assert 0 <= (datetime.now(UTC) - clock).total_seconds() < 120
    assert result["production_snapshot_utc"] == profile.PRODUCTION_SNAPSHOT_AT
    assert result["dataset"]["qualification"]["label"] == "functional smoke"
    assert result["seed"]["disabled_queue_triggers_during_seed"]
    assert result["starter_plans"]["empty"]["feeds"] == []
    assert result["starter_plans"]["restricted"]["feeds"] == []
    assert result["starter_plans"]["sparse"]["feeds"] == []
    assert result["starter_plans"]["prolific"]["feeds"]
    assert result["column_wave"]["columns"] == 12
    assert result["column_wave"]["concurrency"] == 3
    assert result["column_wave"]["statuses"] == {"200": 24}
    assert len(set(result["column_wave"]["paths"])) == 12
    assert len(result["writes"]) == 4
    assert {row["accounts"] for row in result["writes"].values()} == {1, 16}
    for phase in result["measurements"].values():
        assert len(phase["routes"]) == 13
        assert all(row["samples"] == 2 for row in phase["routes"].values())
        assert all(
            row["items_min"] == 24
            for label, row in phase["routes"].items()
            if label.startswith("column-") or label == "latest"
        )


@pytest.mark.integration
def test_recommendation_cli_prepares_real_ready_and_pending_pages(database, monkeypatch, tmp_path):
    result = run_profile_cli(
        monkeypatch, tmp_path, "--recommendations-only", "--prepare-recommendations"
    )
    assert result["complete"] and result["gates"]["passed"]
    assert result["recommendation_preparation"]["cold_build"]["completed"]
    assert result["recommendation_qualification"]["ready"]["qualified"]
    assert not result["recommendation_qualification"]["above_production"]
    pages = result["recommendation_pages"]
    assert pages["pending"]["status"] == "refreshing"
    assert pages["ready_first"]["status"] == "ready"
    assert pages["ready_first"]["generation"] == pages["ready_second"]["generation"]
    for phase in result["measurements"].values():
        assert set(phase["routes"]) == {"recommended-starter", "recommended-ready"}
        assert all(row["items_min"] == row["items_max"] == 24 for row in phase["routes"].values())
        assert phase["routes"]["recommended-starter"]["queries_max"] == 9
        assert phase["routes"]["recommended-ready"]["queries_max"] == 6

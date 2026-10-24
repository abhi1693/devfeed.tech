"""The retention benchmark exercises real migrated SQL and concurrent ingestion."""

import json
import sys
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings

from scripts import job_retention_benchmark
from tests.support.paths import REPO_ROOT


@pytest.mark.integration
def test_benchmark_runs_both_histories_to_convergence_with_real_plans_and_ingestion(
    database, monkeypatch, tmp_path
):
    settings = get_settings()
    baseline = tmp_path / "checkpointed-baseline.py"
    baseline.write_text((REPO_ROOT / "packages/core/src/job_retention.py").read_text())
    output = tmp_path / "retention-smoke.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_retention_benchmark.py",
            "--articles",
            "1000",
            "--baseline-file",
            str(baseline),
            "--output",
            str(output),
            "--backlog-pages",
            "2",
            "--payload-bytes",
            "128",
        ],
    )
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DEVFEED_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DEVFEED_REDIS_URL", settings.redis_url)
    # main changes these settings while switching from default page limits to
    # convergence. Register each variable so the test restores its original value.
    for name, value in {
        "RETENTION_DAYS": "30",
        "PRUNE_BATCH_SIZE": "100",
        "SCAN_BATCH_SIZE": "1000",
        "PAGE_INTERVAL_SECONDS": "30",
        "SWEEP_INTERVAL_SECONDS": "3600",
    }.items():
        monkeypatch.setenv(f"DEVFEED_JOB_PAYLOAD_{name}", value)
    job_retention_benchmark.main()

    report = json.loads(output.read_text())
    assert report["articles"] == 1000
    assert report["jobs_per_kind"] == 3700
    assert report["payload_bytes"] == 128
    assert "PostgreSQL 17." in report["postgres_version"]
    assert len(report["phases"]) == 10
    for implementation in ("baseline", "current"):
        phases = report["phases"]
        backlog = phases[f"{implementation}_backlog"]
        assert backlog["retention"]["calls"] == 2
        assert 0 < backlog["max_pruned_per_call"] <= 200
        assert backlog["plans"]
        convergence = phases[f"{implementation}_convergence_prune1000_scan1000"]
        assert 0 < convergence["max_pruned_per_call"] <= 2000
        assert convergence["pruned"] > 0
        assert convergence["plans"]
        latest = phases[f"{implementation}_latest_only"]
        assert latest["pruned"] == 0
        assert latest["plans"]
        for phase in (backlog, convergence, latest):
            assert phase["ingestion"]["calls"] > 0
            assert phase["wal_bytes_including_ingestion"] >= 0
            assert all(plan["examined_job_rows"] <= 12_000 for plan in phase["plans"])
            assert all("Execution Time" in plan["plan"] for plan in phase["plans"])
        for suffix in ("ordinary_ticks", "ordinary_ticks_no_writer"):
            idle = phases[f"{implementation}_{suffix}"]
            assert idle["retention"]["calls"] == 20
            assert idle["pruned"] == 0
            assert idle["plans"] == []
            assert idle["sql_statements"] == 40
        assert phases[f"{implementation}_ordinary_ticks"]["ingestion"]["calls"] > 0
        assert phases[f"{implementation}_ordinary_ticks_no_writer"]["ingestion"]["calls"] == 0


def test_benchmark_rejects_a_database_without_the_disposable_name_before_migrations(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv(
        "DEVFEED_TEST_DATABASE_URL", "postgresql+psycopg://unit@database.invalid/devfeed"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_retention_benchmark.py",
            "--articles",
            "1000",
            "--baseline-file",
            str(tmp_path / "baseline.py"),
            "--output",
            str(tmp_path / "output.json"),
        ],
    )
    migrate = Mock()
    monkeypatch.setattr(job_retention_benchmark.command, "upgrade", migrate)
    with pytest.raises(SystemExit) as failure:
        job_retention_benchmark.main()
    assert failure.value.code == 2
    assert "dedicated disposable _test database" in capsys.readouterr().err
    migrate.assert_not_called()

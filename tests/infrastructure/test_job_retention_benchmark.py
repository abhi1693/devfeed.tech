"""The retention benchmark exercises real migrated SQL and concurrent ingestion."""

import json
import os
import sys
from unittest.mock import Mock, patch

import pytest
from devfeed_core.config import get_settings
from sqlalchemy import text

from scripts import job_retention_benchmark
from tests.support.paths import REPO_ROOT


@pytest.mark.integration
def test_benchmark_runs_both_histories_to_convergence_with_real_plans_and_ingestion(
    database, monkeypatch, tmp_path
):
    settings = get_settings()
    with database() as session:
        postgres_major = int(session.scalar(text("SHOW server_version_num"))) // 10000
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
            "--cadence-interval-seconds",
            "0.05",
            "--cadence-payload-bytes",
            "4096",
            "--postgres-major",
            str(postgres_major),
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
    assert report["cadence_payload_bytes"] == 4096
    assert report["cadence_article_usage_output_bytes"] == 16384
    assert report["cadence_topic_usage_bytes"] == 1024
    assert report["cadence_subjects_per_kind"] == 1000
    assert report["postgres_major"] == postgres_major
    assert f"PostgreSQL {postgres_major}." in report["postgres_version"]
    assert len(report["phases"]) == 12
    for implementation in ("baseline", "current"):
        phases = report["phases"]
        backlog = phases[f"{implementation}_backlog"]
        assert backlog["retention"]["calls"] == 2
        assert 0 < backlog["max_pruned_per_call"] <= 200
        assert backlog["plans"]
        assert backlog["deep_cursor_plans"]
        convergence = phases[f"{implementation}_convergence_prune1000_scan1000"]
        assert 0 < convergence["max_pruned_per_call"] <= 2000
        assert convergence["pruned"] > 0
        assert convergence["plans"]
        latest = phases[f"{implementation}_latest_only"]
        assert latest["pruned"] == 0
        assert latest["plans"]
        assert latest["deep_cursor_plans"]
        for phase in (backlog, convergence, latest):
            assert phase["ingestion"]["calls"] > 0
            assert phase["wal_bytes_including_ingestion"] >= 0
            assert all(plan["examined_job_rows"] <= 12_000 for plan in phase["plans"])
            assert all("Execution Time" in plan["plan"] for plan in phase["plans"])
            assert len(phase["retention_samples_ms"]) == phase["retention"]["calls"]
            assert len(phase["ingestion_samples_ms"]) == phase["ingestion"]["calls"]
            assert (
                phase["checkpointer_after"]["num_requested"]
                >= phase["checkpointer_before"]["num_requested"]
            )
            assert all(plan["examined_job_rows"] <= 12_000 for plan in phase["deep_cursor_plans"])
        for suffix in ("ordinary_ticks", "ordinary_ticks_no_writer"):
            idle = phases[f"{implementation}_{suffix}"]
            assert idle["retention"]["calls"] == 20
            assert idle["pruned"] == 0
            assert idle["plans"] == []
            assert idle["deep_cursor_plans"] == []
            assert idle["sql_statements"] == 40
        paced = phases[f"{implementation}_deployed_cadence"]
        assert paced["retention"]["calls"] == 5
        assert paced["clock_step_seconds"] == 15
        assert paced["real_tick_interval_seconds"] == 0.05
        assert 0 < paced["max_pruned_per_call"] <= 200
        assert paced["ingestion"]["calls"] > 0
        assert paced["ingestion"]["p99_ms"] >= paced["ingestion"]["p95_ms"]
        assert paced["wait_samples"]
        assert all("query" not in sample for sample in paced["wait_samples"])
        assert (
            paced["forced_checkpoint_after"]["num_requested"]
            >= paced["forced_checkpoint_before"]["num_requested"]
        )
        assert set(paced["wal_counters_delta"]) == {
            "wal_records",
            "wal_fpi",
            "wal_bytes",
            "wal_buffers_full",
        }
        assert phases[f"{implementation}_ordinary_ticks"]["ingestion"]["calls"] > 0
        assert phases[f"{implementation}_ordinary_ticks_no_writer"]["ingestion"]["calls"] == 0


@pytest.mark.integration
def test_benchmark_rejects_a_different_server_major_before_seeding(
    database, monkeypatch, tmp_path, capsys
):
    with database() as session:
        actual_major = int(session.scalar(text("SHOW server_version_num"))) // 10000
    baseline = tmp_path / "baseline.py"
    baseline.write_text((REPO_ROOT / "packages/core/src/job_retention.py").read_text())
    output = tmp_path / "mismatched-postgres.json"
    arguments = [
        "job_retention_benchmark.py",
        "--articles",
        "1000",
        "--baseline-file",
        str(baseline),
        "--output",
        str(output),
    ]
    # PostgreSQL 18 must remain rejected by the production default. On 17,
    # require 18 explicitly to prove that the requested major is enforced too.
    expected_major = 17 if actual_major == 18 else 18
    if actual_major != 18:
        arguments.extend(["--postgres-major", str(expected_major)])
    monkeypatch.setattr(sys, "argv", arguments)
    seed = Mock()
    monkeypatch.setattr(job_retention_benchmark, "seed", seed)
    with patch.dict(os.environ), pytest.raises(SystemExit) as failure:
        job_retention_benchmark.main()
    assert failure.value.code == 2
    assert f"Use PostgreSQL {expected_major}" in capsys.readouterr().err
    seed.assert_not_called()
    assert not output.exists()


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

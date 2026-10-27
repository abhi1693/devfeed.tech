"""The retention benchmark exercises real migrated SQL and concurrent ingestion."""

import json
import os
import sys
import zlib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from devfeed_core.config import get_settings
from sqlalchemy import text

from scripts import job_retention_benchmark
from tests.support.paths import REPO_ROOT


@pytest.mark.integration
def test_seed_restores_analytics_controls_and_retention_capture_after_reseeding(database):
    from datetime import timedelta

    from devfeed_core.analytics_schema import BACKFILL_TABLES
    from devfeed_core.db import get_engine
    from devfeed_core.engagement import prune_article_opens
    from devfeed_core.models import Article, ArticleOpen, utcnow
    from sqlalchemy import func, select

    for iteration in range(2):
        job_retention_benchmark.seed(get_engine(), articles=2, payload_bytes=8)
        with database.begin() as session:
            state = session.execute(
                text(
                    "SELECT enabled,capture_started_at,last_success_at,native_lease_token,"
                    "native_import_complete,native_bootstrap_complete,card_bootstrap_complete "
                    "FROM analytics_pipeline_state"
                )
            ).one()
            assert state == (False, None, None, None, False, False, False)
            assert session.scalar(text("SELECT started FROM analytics_gauge_bootstrap")) is False
            timer = session.execute(
                text(
                    "SELECT policy,epoch,kind_index,shard,cursor,complete,cleanup_pending "
                    "FROM analytics_card_timer_bootstrap"
                )
            ).one()
            assert timer == (None, 0, 0, None, None, False, False)
            assert set(
                session.execute(text("SELECT domain,shard FROM analytics_gauge_refreshes"))
            ) == {
                ("queues", 0),
                ("feed", 0),
                ("sources", 0),
                ("topics", 0),
            }
            cursors = session.execute(
                text("SELECT source_table,complete FROM analytics_bootstrap_cursors")
            ).all()
            assert {row.source_table for row in cursors} == set(BACKFILL_TABLES)
            assert all(not row.complete for row in cursors)
            article = session.scalar(select(Article.id).limit(1))
            session.add(
                ArticleOpen(
                    article_id=article,
                    viewer_key="retention-fixture",
                    opened_hour=utcnow() - timedelta(days=31),
                )
            )

        # Retention remains fail-closed until capture is enabled; then the
        # deleted source row is retained in the durable native ingest queue.
        prune_article_opens(database)
        with database.begin() as session:
            assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 1
            session.execute(text("UPDATE analytics_pipeline_state SET enabled=true"))
        prune_article_opens(database)
        with database.begin() as session:
            assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 0
            captured = session.execute(
                text(
                    "SELECT operation,record,before_record FROM analytics_ingest_events "
                    "WHERE source_table='article_opens'"
                )
            ).one()
            assert captured.operation == "I"
            assert captured.record["viewer_key"] == "retention-fixture"
            assert captured.before_record is None
            if iteration == 0:
                session.execute(
                    text("UPDATE analytics_pipeline_state SET native_bootstrap_complete=true")
                )
                session.execute(text("UPDATE analytics_gauge_bootstrap SET started=true"))
                session.execute(
                    text(
                        "UPDATE analytics_card_timer_bootstrap SET "
                        "policy='stale',epoch=9,kind_index=3,complete=true,cleanup_pending=true"
                    )
                )
                session.execute(text("UPDATE analytics_bootstrap_cursors SET complete=true"))
                session.execute(
                    text("INSERT INTO analytics_gauge_refreshes(domain,shard) VALUES('feed',100)")
                )


@pytest.mark.integration
def test_benchmark_runs_both_histories_to_convergence_with_real_plans_and_ingestion(
    database, monkeypatch, tmp_path
):
    settings = get_settings()
    with database() as session:
        postgres_major = int(session.scalar(text("SHOW server_version_num"))) // 10000
    baseline = tmp_path / "checkpointed-baseline.py"
    baseline.write_text((REPO_ROOT / "packages/core/src/job_retention.py").read_text())
    output = tmp_path / "1000.json"
    monkeypatch.setattr(job_retention_benchmark, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "job_retention_benchmark.py",
            "--articles",
            "1000",
            "--baseline-file",
            str(baseline),
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
    assert report["fixture_payload_format"] == "shake256-base64-v1"
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
    output = tmp_path / "1000.json"
    monkeypatch.setattr(job_retention_benchmark, "OUTPUT_ROOT", tmp_path)
    arguments = [
        "job_retention_benchmark.py",
        "--articles",
        "1000",
        "--baseline-file",
        str(baseline),
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
        ],
    )
    migrate = Mock()
    monkeypatch.setattr(job_retention_benchmark.command, "upgrade", migrate)
    with pytest.raises(SystemExit) as failure:
        job_retention_benchmark.main()
    assert failure.value.code == 2
    assert "dedicated disposable _test database" in capsys.readouterr().err
    migrate.assert_not_called()


def test_fixture_payloads_are_reproducible_domain_separated_and_high_entropy():
    payload = job_retention_benchmark.fixture_payload(65536, "history-input")
    assert len(payload) == 65536
    assert payload.isascii()
    assert payload == job_retention_benchmark.fixture_payload(65536, "history-input")
    assert payload[:128] == job_retention_benchmark.fixture_payload(128, "history-input")
    assert payload != job_retention_benchmark.fixture_payload(65536, "cadence-input")
    assert len(zlib.compress(payload.encode())) > len(payload) * 0.7
    assert job_retention_benchmark.fixture_payload(0, "history-input") == ""


@pytest.mark.parametrize("path", ["../outside.json", "/outside.json", "child/output.json"])
def test_benchmark_has_no_cli_output_path_override(monkeypatch, tmp_path, path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark", "--articles", "1000", "--baseline-file", "baseline.py", "--output", path],
    )
    migrate = Mock()
    monkeypatch.setattr(job_retention_benchmark.command, "upgrade", migrate)
    with pytest.raises(SystemExit) as failure:
        job_retention_benchmark.main()
    assert failure.value.code == 2
    migrate.assert_not_called()
    assert not (tmp_path / "1000.json").exists()


@pytest.mark.parametrize("articles", ["0", "-1"])
def test_benchmark_rejects_nonpositive_articles_before_output_or_database(monkeypatch, articles):
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--articles", articles, "--baseline-file", "baseline.py"]
    )
    writer = Mock()
    monkeypatch.setattr(job_retention_benchmark, "report_writer", writer)
    with pytest.raises(SystemExit) as failure:
        job_retention_benchmark.main()
    assert failure.value.code == 2
    writer.assert_not_called()


@pytest.mark.parametrize(
    "unsafe", ["root-link", "file-link", "hardlink", "fifo", "directory", "traversal", "relative"]
)
def test_benchmark_rejects_unsafe_outputs_before_migrations(monkeypatch, tmp_path, unsafe):
    root = tmp_path / "reports"
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "1000.json"
    protected.write_text("Keep outside data")
    reader = None
    if unsafe == "root-link":
        root.symlink_to(outside, target_is_directory=True)
    elif unsafe == "traversal":
        root = root / ".." / "outside"
    elif unsafe == "relative":
        root = Path("reports/retention")
    else:
        root.mkdir()
        output = root / "1000.json"
        if unsafe == "file-link":
            output.symlink_to(protected)
        elif unsafe == "hardlink":
            output.hardlink_to(protected)
        elif unsafe == "directory":
            output.mkdir()
        else:
            os.mkfifo(output)
            reader = os.open(output, os.O_RDONLY | os.O_NONBLOCK)
    monkeypatch.setattr(job_retention_benchmark, "OUTPUT_ROOT", root)
    monkeypatch.setenv(
        "DEVFEED_TEST_DATABASE_URL", "postgresql+psycopg://unit@database.invalid/disposable_test"
    )
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--articles", "1000", "--baseline-file", "baseline.py"]
    )
    migrate = Mock()
    monkeypatch.setattr(job_retention_benchmark.command, "upgrade", migrate)
    try:
        with pytest.raises(SystemExit) as failure:
            job_retention_benchmark.main()
        assert failure.value.code == 2
        migrate.assert_not_called()
        assert protected.read_text() == "Keep outside data"
    finally:
        if reader is not None:
            os.close(reader)


def test_report_writer_reuses_validated_descriptor_when_path_is_replaced(monkeypatch, tmp_path):
    root = tmp_path / "reports"
    protected = tmp_path / "outside.json"
    protected.write_text("Keep outside data")
    monkeypatch.setattr(job_retention_benchmark, "OUTPUT_ROOT", root)
    with job_retention_benchmark.report_writer(1000) as write_report:
        output = root / "1000.json"
        original = root / "original.json"
        output.rename(original)
        output.symlink_to(protected)
        write_report({"phase": "baseline"})
        write_report({"phase": "head"})
    assert json.loads(original.read_text()) == {"phase": "head"}
    assert protected.read_text() == "Keep outside data"


def test_report_writer_closes_descriptors_and_invalidates_stale_results_on_failure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(job_retention_benchmark, "OUTPUT_ROOT", tmp_path)
    opened = []
    actual_open = os.open

    def track_open(*args, **kwargs):
        descriptor = actual_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", track_open)
    with pytest.raises(RuntimeError), job_retention_benchmark.report_writer(1000):
        raise RuntimeError("Setup failed")
    assert not (tmp_path / "1000.json").exists()
    (tmp_path / "1000.json").write_text("Existing report")
    with pytest.raises(RuntimeError), job_retention_benchmark.report_writer(1000):
        raise RuntimeError("Setup failed")
    assert (tmp_path / "1000.json").read_text() == ""
    with (
        pytest.raises(RuntimeError),
        job_retention_benchmark.report_writer(1000) as write_report,
    ):
        write_report({"phase": "current-run-baseline"})
        raise RuntimeError("Candidate failed")
    assert json.loads((tmp_path / "1000.json").read_text()) == {"phase": "current-run-baseline"}
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("articles", [0, -1, "../outside"])
def test_report_writer_rejects_invalid_numeric_filenames_before_open(monkeypatch, articles):
    opened = Mock()
    monkeypatch.setattr(os, "open", opened)
    with pytest.raises(ValueError), job_retention_benchmark.report_writer(articles):
        pytest.fail("Unsafe article count was accepted")
    opened.assert_not_called()

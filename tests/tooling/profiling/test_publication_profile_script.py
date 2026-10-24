"""Publication benchmarks fail safely and retain trustworthy measurement evidence."""

import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql.psycopg import dialect

from scripts import profile_publication_reports as profile


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///publication_test",
        "postgresql://ci@production.invalid/publication_test",
        "postgresql://ci@localhost/publication",
        "postgresql://ci@localhost",
        "postgresql://ci@localhost/publication_test?host=production.invalid",
        "postgresql://ci@localhost/publication_test?dbname=production",
        "postgresql://ci@localhost/publication_test?service=production",
    ],
)
def test_target_validation_refuses_non_disposable_or_redirected_databases(url):
    with pytest.raises(ValueError, match="disposable local PostgreSQL"):
        profile.validate_target(url)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_target_validation_refuses_inherited_libpq_routing(monkeypatch, name):
    monkeypatch.setenv(name, "production.invalid")
    with pytest.raises(ValueError, match="without overrides"):
        profile.validate_target("postgresql://ci@localhost/publication_test")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_target_validation_accepts_explicit_local_test_database(host):
    url = profile.validate_target(f"postgresql+psycopg://ci@{host}:54321/publication_test")
    assert url.host == host
    assert url.port == 54321
    assert url.database == "publication_test"


def test_failed_ingestion_signals_readiness_without_claiming_commits(measurement_engine):
    engine, _ = measurement_engine
    engine.connect.side_effect = RuntimeError("database disconnected")
    ingestion = profile.Ingestion(engine)
    ingestion.run()
    assert ingestion.ready.is_set()
    assert ingestion.errors == ["database disconnected"]
    assert ingestion.rows == 0
    assert ingestion.samples_ms == []


def work_report(*, candidate=None):
    return {
        "strategies": {
            "baseline": {"plan": {"Plan": {"Shared Hit Blocks": 100, "Shared Read Blocks": 20}}},
            "candidate": {
                "plan": {"Plan": candidate or {"Shared Hit Blocks": 10, "Shared Read Blocks": 20}},
                "result": [32, 10, 60],
            },
        }
    }


def test_work_gate_counts_reads_and_hits_and_reports_temporary_budget():
    report = work_report(
        candidate={"Shared Hit Blocks": 10, "Shared Read Blocks": 20, "Temp Written Blocks": 2}
    )
    assert profile.check_work(report) == {
        "candidate_buffer_fraction": 0.25,
        "no_correlated_subplan": True,
        "temporary_bytes": 16384,
        "temporary_budget_bytes": 16384,
    }


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        ({"Shared Hit Blocks": 90}, "buffer work regressed"),
        ({"Shared Read Blocks": 91}, "buffer work regressed"),
        (
            {"Plans": [{"Plans": [{"Parent Relationship": "SubPlan"}]}]},
            "per-publication correlated SubPlan",
        ),
        ({"Temp Read Blocks": 1, "Temp Written Blocks": 2}, "temporary work"),
    ],
)
def test_work_gate_rejects_repeated_probes_and_excessive_buffer_or_temporary_work(
    candidate, message
):
    with pytest.raises(AssertionError, match=message):
        profile.check_work(work_report(candidate=candidate))


def test_backend_cpu_parses_process_names_with_spaces_and_parentheses(monkeypatch):
    fields = ["S", *("0" for _ in range(10)), "22", "11"]
    connection = MagicMock()
    connection.scalar.return_value = "41 (postgres (report worker)) " + " ".join(fields)
    monkeypatch.setattr(profile.os, "sysconf", lambda name: 100)
    assert profile.backend_cpu(connection) == 0.33


def test_seed_batches_never_loses_the_partial_final_batch():
    connection = MagicMock()
    profile.seed_batches(connection, "SELECT :first,:last,:tag", 5001, {"tag": "fixture"})
    assert [call.args[1] for call in connection.execute.call_args_list] == [
        {"first": 1, "last": 5000, "tag": "fixture"},
        {"first": 5001, "last": 5001, "tag": "fixture"},
    ]


@pytest.fixture
def measurement_engine():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.one.return_value = (32, 10, 60)
    connection.execute.return_value.mappings.return_value.one.return_value = {
        "generic_plans": 3,
        "custom_plans": 5,
    }
    connection.scalar.return_value = [{"Plan": {"Node Type": "Aggregate"}}]
    return engine, connection


@pytest.mark.parametrize("mode", [None, "force_custom_plan", "force_generic_plan", "auto"])
def test_measurement_excludes_warmups_and_retains_prepared_plan_counters(
    monkeypatch, measurement_engine, mode
):
    engine, connection = measurement_engine
    cpu = iter([0.1, 0.11, 0.2, 0.22])
    monkeypatch.setattr(profile, "backend_cpu", lambda _: next(cpu))
    prepared = ("publication_plan", "SELECT 32,10,60", mode) if mode else None
    result = profile.measure(engine, "SELECT :count,10,60", {"count": 32}, 2, False, prepared)
    assert result["result"] == [32, 10, 60]
    assert len(result["samples_ms"]) == len(result["application_thread_cpu_samples_ms"]) == 2
    assert result["backend_cpu_samples_ms"] == pytest.approx([10, 20])
    assert result["plan"] == {"Plan": {"Node Type": "Aggregate"}}
    commands = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert commands.count("SELECT :count,10,60") == (9 if mode == "auto" else 3)
    if mode:
        assert result["prepared_plan_counts"] == {"generic_plans": 3, "custom_plans": 5}
        assert "PREPARE publication_plan AS SELECT 32,10,60" in commands
        assert commands[-1] == "DEALLOCATE publication_plan"
    else:
        assert "prepared_plan_counts" not in result
    engine.connect.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", ["timeout", "writer_error", "thread_alive"])
def test_measurement_stops_concurrent_writer_on_startup_or_measurement_failure(
    monkeypatch, measurement_engine, failure
):
    engine, connection = measurement_engine
    ingestion = MagicMock()
    ingestion.ready.wait.return_value = failure != "timeout"
    ingestion.thread.is_alive.return_value = failure == "thread_alive"
    ingestion.errors = ["write failed"] if failure == "writer_error" else []
    monkeypatch.setattr(profile, "Ingestion", lambda _: ingestion)
    monkeypatch.setattr(profile, "backend_cpu", lambda _: 0)
    if failure != "timeout":
        connection.execute.side_effect = RuntimeError("report failed")
    with pytest.raises(RuntimeError):
        profile.measure(engine, "SELECT 1", {}, 1, True)
    ingestion.stop.set.assert_called_once()
    ingestion.thread.join.assert_called_once_with(10)


@pytest.mark.parametrize("unequal", [False, True])
def test_prepared_profiles_keep_all_plan_modes_and_preserve_mismatched_evidence(
    monkeypatch, unequal
):
    calls, snapshots = [], []

    def measure(engine, sql, params, repeats, concurrent, prepared):
        calls.append((sql, prepared))
        result = [32, 10, 60 if not unequal or prepared[0].startswith("profile_baseline") else 61]
        return {"result": result, "plan": {"Plan": {"Shared Hit Blocks": 1}}}

    engine = SimpleNamespace(dialect=dialect())
    monkeypatch.setattr(profile, "measure", measure)
    monkeypatch.setattr(profile, "check_work", lambda _: {"no_correlated_subplan": True})

    def preserve(reports):
        snapshots.append(json.loads(json.dumps(reports)))

    if unequal:
        with pytest.raises(AssertionError, match="Prepared report results differ"):
            profile.prepared_profiles(engine, 2, 100000, preserve)
        assert "result_equality" not in snapshots[-1][0]
    else:
        reports = profile.prepared_profiles(engine, 2, 100000, preserve)
        assert {(row["days"], row["plan_cache_mode"]) for row in reports} == {
            (days, mode)
            for days in (1, 7, 30)
            for mode in ("force_custom_plan", "force_generic_plan", "auto")
        }
        assert all(row["result_equality"] for row in reports)
        assert all("work_checks" in row for row in reports if row["days"] == 30)
        assert len(calls) == 18
        assert snapshots[-1] == reports


@pytest.fixture
def output_repository(monkeypatch, tmp_path):
    monkeypatch.setattr(
        profile, "__file__", str(tmp_path / "scripts/profile_publication_reports.py")
    )
    return tmp_path


@pytest.mark.parametrize("absolute", [False, True])
def test_output_validation_accepts_only_json_artifacts_beneath_publication_reports(
    output_repository, absolute
):
    relative = Path("reports/publication/nested/result.json")
    requested = output_repository / relative if absolute else relative
    target = profile.validate_output(requested)
    assert target == output_repository / relative
    profile.write_report(target, {"result": "first"})
    profile.write_report(target, {"result": "replacement"})
    assert json.loads(target.read_text()) == {"result": "replacement"}
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize(
    "relative",
    [
        "outside.json",
        "reports/publication/../../outside.json",
        "reports/publication/result.txt",
        "reports/publication",
    ],
)
def test_output_validation_rejects_paths_outside_artifact_boundary(output_repository, relative):
    with pytest.raises(ValueError):
        profile.validate_output(Path(relative))
    assert not (output_repository / "outside.json").exists()


@pytest.mark.parametrize("kind", ["root", "ancestor", "target", "directory", "file_parent"])
def test_output_validation_rejects_symlink_escapes_and_directory_or_file_conflicts(
    output_repository, tmp_path, kind
):
    root = output_repository / "reports/publication"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text("original")
    target = root / "nested/result.json"
    if kind == "root":
        root.parent.mkdir()
        root.symlink_to(outside, target_is_directory=True)
    elif kind == "ancestor":
        root.mkdir(parents=True)
        (root / "nested").symlink_to(outside, target_is_directory=True)
    elif kind == "target":
        target.parent.mkdir(parents=True)
        target.symlink_to(outside / "result.json")
    elif kind == "directory":
        target.mkdir(parents=True)
    else:
        root.mkdir(parents=True)
        (root / "nested").write_text("parent is a file")
    with pytest.raises(ValueError):
        profile.validate_output(target)
    assert (outside / "result.json").read_text() == "original"


def test_report_serialization_failure_preserves_existing_artifact(output_repository):
    target = profile.validate_output(Path("reports/publication/result.json"))
    profile.write_report(target, {"result": "previous good evidence"})
    with pytest.raises(TypeError):
        profile.write_report(target, {"result": object()})
    assert json.loads(target.read_text()) == {"result": "previous good evidence"}
    assert list(target.parent.iterdir()) == [target]


def test_output_atomic_replacement_does_not_overwrite_an_external_hardlink(output_repository):
    target = profile.validate_output(Path("reports/publication/result.json"))
    target.parent.mkdir(parents=True)
    outside = output_repository / "external.json"
    outside.write_text("original")
    target.hardlink_to(outside)
    profile.write_report(target, {"result": "new evidence"})
    assert outside.read_text() == "original"
    assert json.loads(target.read_text()) == {"result": "new evidence"}


def run_cli(monkeypatch, root, *options):
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", "postgresql://ci@localhost/publication_test")
    output = root / "reports/publication/result.json"
    monkeypatch.setattr(sys, "argv", ["publication-profile", "--output", str(output), *options])
    profile.main()
    return json.loads(output.read_text())


@pytest.mark.parametrize(
    "options", [("--rows", "99"), ("--rows", "1000001"), ("--repeats", "0"), ("--repeats", "21")]
)
def test_cli_refuses_unbounded_profiles_before_opening_database(
    monkeypatch, output_repository, options
):
    monkeypatch.setattr(profile, "create_engine", lambda *args, **kwargs: pytest.fail("Opened DB"))
    with pytest.raises(SystemExit) as error:
        run_cli(monkeypatch, output_repository, *options)
    assert error.value.code == 2


@pytest.mark.parametrize("failure", [None, "version", "different_results", "uniform_fixture"])
def test_cli_retains_evidence_and_disposes_engine_after_report_failures(
    monkeypatch, output_repository, measurement_engine, failure
):
    engine, connection = measurement_engine
    engine.dialect = dialect()
    engine.dialect.server_version_info = (18, 0) if failure == "version" else (17, 5)
    connection.dialect = engine.dialect
    connection.scalar.return_value = "PostgreSQL fixture"
    connection.execute.return_value.all.return_value = [("work_mem", "4MB")]
    monkeypatch.setattr(profile, "create_engine", lambda *_args, **_kwargs: engine)
    seeded, calls, prepared = [], [], []
    monkeypatch.setattr(profile, "seed", lambda _engine, rows: seeded.append(rows))

    def measure(_engine, sql, params, repeats, concurrent):
        calls.append((sql, concurrent))
        result = [32, 10, 60]
        if failure == "different_results" and len(calls) == 2:
            result[2] = 61
        if failure == "uniform_fixture":
            result[1] = 0
        return {"result": result, "median_ms": 1, "plan": {"Plan": {}}}

    def prepared_profiles(_engine, repeats, rows, preserve):
        prepared.append((repeats, rows))
        preserve([{"prepared": True}])

    monkeypatch.setattr(profile, "measure", measure)
    monkeypatch.setattr(profile, "prepared_profiles", prepared_profiles)
    monkeypatch.setattr(profile, "check_work", lambda _: {"no_correlated_subplan": True})
    if failure:
        with pytest.raises((ValueError, AssertionError)):
            run_cli(monkeypatch, output_repository)
        output = output_repository / "reports/publication/result.json"
        if failure != "version":
            report = json.loads(output.read_text())["reports"][0]
            assert "result_equality" not in report
            assert set(report["strategies"]) == {"baseline", "candidate"}
    else:
        output = run_cli(
            monkeypatch, output_repository, "--seed", "--inline-comparison", "--prepared"
        )
        assert seeded == [100000]
        assert prepared == [(5, 100000)]
        assert output["prepared_reports"] == [{"prepared": True}]
        assert output["fixture_analysis_job_rows"] == 165000
        assert {(report["days"], report["concurrent"]) for report in output["reports"]} == {
            (days, concurrent) for days in (1, 7, 30) for concurrent in (False, True)
        }
        assert all(report["result_equality"] for report in output["reports"])
        assert all("candidate_inline" in report["strategies"] for report in output["reports"])
        assert all("work_checks" in report for report in output["reports"] if report["days"] == 30)
    engine.dispose.assert_called_once()


@pytest.mark.integration
def test_seed_produces_mixed_equivalent_reports_and_clears_previous_fixture(database):
    engine = database.kw["bind"]
    for rows in (203, 100):
        profile.seed(engine, rows)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM articles")) == rows
            assert (
                connection.scalar(text("SELECT count(*) FROM article_reviews"))
                == rows * 3 + rows // 201 * 24
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM article_analysis_jobs"))
                == rows * 165 // 100
            )
            for days in (1, 7, 30):
                start = profile.NOW - timedelta(days=days)
                baseline = connection.execute(
                    text(profile.BASELINE), {"start": start, "now": profile.NOW}
                ).one()
                candidate = connection.execute(
                    profile._publication_automation_query(start, profile.NOW)
                ).one()
                assert baseline == candidate
                assert 0 < candidate[1] < candidate[0]


@pytest.mark.integration
def test_real_measurements_include_prepared_counters_and_durable_concurrent_writes(database):
    engine = database.kw["bind"]
    profile.seed(engine, 100)
    result = profile.measure(engine, "SELECT 32,10,60", {}, 2, True)
    assert result["result"] == [32, 10, 60]
    writer = result["concurrent_ingestion"]
    assert writer["commits"] >= 1
    assert writer["wal_bytes"] > 0
    assert writer["errors"] == []
    assert writer["commits_per_second"] > 0
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM articles")) == 100 + writer["commits"]
        assert (
            connection.scalar(text("SELECT count(*) FROM article_reviews"))
            == 300 + writer["commits"]
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM article_analysis_jobs"))
            == 165 + writer["commits"]
        )
    for mode in ("force_custom_plan", "force_generic_plan", "auto"):
        result = profile.measure(
            engine,
            "EXECUTE publication_fixture",
            {},
            2,
            False,
            ("publication_fixture", "SELECT 32,10,60", mode),
        )
        assert result["result"] == [32, 10, 60]
        counters = result["prepared_plan_counts"]
        assert counters["generic_plans"] + counters["custom_plans"] >= 4

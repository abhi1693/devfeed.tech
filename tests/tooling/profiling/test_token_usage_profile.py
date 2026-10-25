"""Benchmark safeguards, paired measurements and production-volume accounting."""

import copy
import importlib.util
import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "token_usage_profile", REPO_ROOT / "scripts/profile_token_usage.py"
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class Result:
    def __init__(self, value):
        self.value = value

    def __iter__(self):
        return iter(self.value)

    def mappings(self):
        return self

    def one(self):
        return self.value

    def scalar_one(self):
        return self.value


class Connection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def exec_driver_sql(self, statement):
        self.calls.append((statement, None))
        return Result(None)

    def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return Result(None)

    def commit(self):
        self.commits += 1


class Engine:
    def __init__(self, connection):
        self.connection = connection
        self.disposed = False

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection

    def dispose(self):
        self.disposed = True


@pytest.fixture
def disposable_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DEVFEED_TEST_DATABASE_URL",
        "postgresql+psycopg://devfeed:local@127.0.0.1/devfeed_token_profile_test",
    )
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(profile, "ROOT", tmp_path)


def test_report_destination_is_fixed_to_the_checkout_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    destination = tmp_path / profile.REPORT_NAME
    profile.write_report({"observed_at": profile.NOW, "valid": True})
    assert json.loads(destination.read_text()) == {
        "observed_at": str(profile.NOW),
        "valid": True,
    }
    assert destination.stat().st_mode & 0o777 == 0o600
    destination.chmod(0o644)
    profile.write_report({"replacement": True})
    assert json.loads(destination.read_text()) == {"replacement": True}
    assert destination.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("target", ["directory", "file"])
def test_report_destination_rejects_preexisting_symlinks(tmp_path, monkeypatch, target):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(profile, "ROOT", checkout)
    if target == "directory":
        (checkout / "reports").symlink_to(outside, target_is_directory=True)
    else:
        destination = checkout / profile.REPORT_NAME
        destination.parent.mkdir(parents=True)
        destination.symlink_to(outside / "unrelated.json")
    with pytest.raises(OSError):
        profile.write_report({"overwrite": True})
    assert not list(outside.iterdir())


def test_report_write_refuses_a_symlink_replacing_prior_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    profile.write_report({"valid": True})
    destination = tmp_path / profile.REPORT_NAME
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("preserve")
    destination.unlink()
    destination.symlink_to(unrelated)
    with pytest.raises(OSError):
        profile.write_report({"overwrite": True})
    assert unrelated.read_text() == "preserve"


@pytest.mark.parametrize("kind", ["hardlink", "fifo"])
def test_report_writer_rejects_nonprivate_files_without_truncation(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    destination = tmp_path / profile.REPORT_NAME
    destination.parent.mkdir(parents=True)
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text("preserve")
    if kind == "hardlink":
        destination.hardlink_to(unrelated)
    else:
        os.mkfifo(destination)
    # Opening a FIFO with no reader must fail promptly instead of blocking CI.
    with pytest.raises((ValueError, OSError)):
        profile.write_report({"overwrite": True})
    assert unrelated.read_text() == "preserve"


def test_open_directory_descriptor_prevents_parent_symlink_races(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open = os.open

    def swap_parent(path, flags, *args, **kwargs):
        if path == "profile.json":
            parent = tmp_path / "reports/token-usage"
            parent.rename(tmp_path / "original-reports")
            parent.symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(profile.os, "open", swap_parent)
    profile.write_report({"safe": True})
    assert not list(outside.iterdir())
    assert json.loads((tmp_path / "original-reports/profile.json").read_text()) == {"safe": True}


def test_report_writer_rejects_an_open_fifo_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "ROOT", tmp_path)
    destination = tmp_path / profile.REPORT_NAME
    destination.parent.mkdir(parents=True)
    os.mkfifo(destination)
    reader = os.open(destination, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(ValueError, match="regular file with one link"):
            profile.write_report({"overwrite": True})
        assert os.read(reader, 4096) == b""
    finally:
        os.close(reader)


def test_cli_cannot_select_an_arbitrary_output_path(disposable_environment):
    with pytest.raises(SystemExit) as error:
        profile.main(["--output", "../../unrelated.json"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local_test",
        "postgresql://devfeed@production/devfeed_test",
        "postgresql://devfeed@127.0.0.1/devfeed",
        "postgresql://devfeed@127.0.0.1/devfeed_test?host=production",
        "postgresql://devfeed@127.0.0.1/devfeed_test?options=-csearch_path=public",
        "postgresql://devfeed@/devfeed_test",
    ],
)
def test_unsafe_database_routes_fail_before_connection(disposable_environment, monkeypatch, url):
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", url)
    monkeypatch.setattr(
        profile, "create_engine", lambda *_args, **_kwargs: pytest.fail("connected")
    )
    with pytest.raises(SystemExit) as error:
        profile.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("override", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_libpq_routing_overrides_are_refused(disposable_environment, monkeypatch, override):
    monkeypatch.setenv(override, "remote")
    with pytest.raises(SystemExit) as error:
        profile.main([])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["--articles", "99"],
        ["--articles", "1000001"],
        ["--repeats", "0"],
        ["--repeats", "11"],
        ["--ingestion-rate", "0"],
        ["--ingestion-rate", "101"],
        ["--articles", "100", "200"],
    ],
)
def test_cli_bounds_prevent_unbounded_or_ambiguous_runs(disposable_environment, arguments):
    with pytest.raises(SystemExit) as error:
        profile.main(arguments)
    assert error.value.code == 2


def test_prepared_queries_keep_boundaries_and_both_plan_strategies():
    start = profile.NOW - timedelta(days=30)
    for builder, materialized in (
        (profile.original_statement, True),
        (profile.current_statement, False),
    ):
        statement = builder(start, profile.NOW)
        sql, arguments = profile.prepared_sql(statement)
        assert ("MATERIALIZED" in sql) is materialized
        assert "UNION ALL" in sql
        assert sql.count("finished_at >=") == 3
        assert sql.count("finished_at <=") == 3
        assert "succeeded" in arguments and "failed" in arguments
        assert "^[0-9]{1,18}$" in arguments
        assert "UTC" in arguments and "totalTokens" in arguments
        assert "2026-09-24" in arguments and "2026-10-24" in arguments
        assert "succeeded" not in sql
        assert "$1" in sql
        if not materialized:
            assert sql.count("OFFSET 0") == 6


@pytest.mark.parametrize("mode", ["custom", "generic"])
def test_measurement_config_uses_production_settings_and_cached_plan_mode(mode):
    connection = Connection()
    profile.configure(connection, mode)
    statements = [statement for statement, _parameters in connection.calls]
    assert "SET work_mem='4MB'" in statements
    assert "SET jit=on" in statements
    assert "SET max_parallel_workers_per_gather=2" in statements
    assert "SET statement_timeout='90s'" in statements
    assert f"SET plan_cache_mode='force_{mode}_plan'" in statements
    assert connection.commits == 1


@pytest.mark.parametrize("articles,research", [(100, 73), (300000, 219000)])
def test_seed_retains_large_histories_and_resets_its_own_timeout(articles, research):
    connection = Connection()
    profile.seed(Engine(connection), articles)
    assert connection.calls[0][0] == "SET LOCAL statement_timeout='10min'"
    assert connection.calls[1][0] == "TRUNCATE articles,topic_proposals CASCADE"
    assert any("ON COMMIT DROP" in statement for statement, _parameters in connection.calls)
    inserts = {
        statement.split()[2]: (statement, parameters)
        for statement, parameters in connection.calls
        if statement.startswith("INSERT INTO")
    }
    for table in ("article_analysis_jobs", "topic_analysis_jobs"):
        statement, parameters = next(
            (sql, params)
            for sql, params in connection.calls
            if sql.startswith(f"INSERT INTO {table}") and "provider_metadata" in sql
        )
        assert parameters["jobs"] == 2 * articles
        assert "2592000" in statement and "12960000" in statement
        assert "n%13=0" in statement and "n%5=0" in statement
        assert "999999999999999999" in statement and "broken" in statement
    assert inserts["research_verification_jobs"][1] == {"research": research}
    assert "unfinished-" in inserts["article_analysis_jobs"][0]
    assert [sql for sql, _parameters in connection.calls if sql.startswith("ANALYZE")] == [
        f"ANALYZE {table}"
        for table in (
            "articles",
            "topic_proposals",
            "article_analysis_jobs",
            "topic_analysis_jobs",
            "research_verification_jobs",
        )
    ]


def test_history_and_relation_metadata_use_exact_counts_and_include_toast():
    connection = Connection()

    def relation_query(statement):
        sql = str(statement)
        assert "pg_total_relation_size(c.reltoastrelid)" in sql
        assert "pg_relation_size(c.oid)" in sql
        assert "research_verification_jobs" in sql
        return Result([{"relname": "articles", "toast_bytes": 4096}])

    connection.execute = relation_query
    assert profile.relation_sizes(connection) == [{"relname": "articles", "toast_bytes": 4096}]
    counts = {"total_rows": 500, "one_day_rows": 2, "seven_day_rows": 30, "thirty_day_rows": 99}

    def execute(statement, parameters):
        sql = str(statement)
        assert "count(*) total_rows" in sql
        assert "count(DISTINCT finished_at)" in sql
        assert "status IN ('succeeded','failed')" in sql
        assert "finished_at<=:now" in sql
        assert parameters == {"now": profile.NOW}
        return Result(counts)

    connection.execute = execute
    assert profile.history_counts(connection) == {kind: counts for kind, _model in profile.MODELS}


def test_postgres_cpu_reads_cgroup_usage_and_refuses_unsupported_hosts(monkeypatch):
    assert profile.postgres_cpu(None) is None
    calls = []

    def check_output(arguments, text):
        calls.append(arguments)
        assert text is True
        return "1234\n"

    monkeypatch.setattr(profile.subprocess, "check_output", check_output)
    files = {
        "/proc/1234/cgroup": "0::/system.slice/docker-local.scope\n",
        "/sys/fs/cgroup/system.slice/docker-local.scope/cpu.stat": (
            "usage_usec 2345\nuser_usec 1234\n"
        ),
    }
    monkeypatch.setattr(Path, "read_text", lambda path: files[str(path)])
    assert profile.postgres_cpu("local-postgres") == 2345
    assert calls == [["docker", "inspect", "--format", "{{.State.Pid}}", "local-postgres"]]
    files["/proc/1234/cgroup"] = "1:cpu:/legacy\n"
    with pytest.raises(RuntimeError, match="cgroup v2"):
        profile.postgres_cpu("local-postgres")


def execution_plan(*, groups=30, buffers=100, spill=False):
    return {
        "Execution Time": 42,
        "Plan": {
            "Node Type": "Append",
            "Shared Hit Blocks": buffers,
            "Shared Read Blocks": 10,
            "Temp Written Blocks": 1 if spill else 0,
            "Plans": [
                {
                    "Node Type": "Aggregate",
                    "Strategy": "Hashed",
                    "Plan Rows": groups,
                    "Actual Rows": 30,
                    "Peak Memory Usage": 40,
                    "Disk Usage": 8 if spill else 0,
                    "Plans": [
                        {"Node Type": "Sort", "Sort Space Type": "Disk" if spill else "Memory"}
                    ],
                }
            ],
        },
    }


@pytest.fixture
def measurement(monkeypatch):
    class MeasuredConnection(Connection):
        def __init__(self):
            super().__init__()
            self.plans = {name: execution_plan() for name in ("original", "current")}
            self.rows = {
                name: [("2026-10-24", "article_analysis", 10, 1, 0, 100)] for name in self.plans
            }

        def exec_driver_sql(self, statement):
            super().exec_driver_sql(statement)
            name = "original" if "token_original" in statement else "current"
            if statement.startswith("EXPLAIN"):
                return Result([self.plans[name]])
            if statement.startswith("EXECUTE"):
                return Result(self.rows[name])
            return Result(None)

    writers = []

    class Writer:
        def __init__(self, engine, rate):
            assert rate == 20
            self.samples, self.count = [2, 3], 2
            writers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.count += 1

    connection = MeasuredConnection()
    monkeypatch.setattr(profile, "Ingestion", Writer)
    monkeypatch.setattr(profile, "postgres_cpu", lambda _container: None)
    monkeypatch.setattr(profile.time, "perf_counter", CounterClock())
    monkeypatch.setattr(profile.time, "thread_time", CounterClock())
    return SimpleNamespace(connection=connection, engine=Engine(connection), writers=writers)


class CounterClock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 0.01
        return self.tick


def test_measure_compares_equal_results_alternates_order_and_excludes_warmup(measurement):
    report = profile.measure(measurement.engine, 7, "custom", 2, None, 20)
    assert report["result_equality"] is True
    assert report["returned_groups"] == 1
    assert len(report["result_sha256"]) == 64
    assert all(report["checks"].values())
    assert len(measurement.writers) == 6
    assert [
        "original" if "token_original" in sql else "current"
        for sql, _parameters in measurement.connection.calls
        if sql.startswith("EXECUTE")
    ] == ["original", "current", "current", "original", "original", "current"]
    for strategy in report["strategies"].values():
        assert len(strategy["samples"]) == len(strategy["ingestion"]) == 2
        assert strategy["median_ms"] == pytest.approx(10)
        assert strategy["samples"][0]["database_container_cpu_ms"] is None
        assert strategy["aggregates"][0]["Peak Memory Usage"] == 40
        assert strategy["plan"]["Execution Time"] == 42
    assert measurement.connection.calls[-1][0] == "DEALLOCATE ALL"
    assert measurement.connection.commits == 2


def test_measure_fails_if_token_accounting_changes(measurement):
    measurement.connection.rows["current"] = [("2026-10-24", "article_analysis", 11, 1, 0, 100)]
    with pytest.raises(AssertionError, match="Token accounting changed"):
        profile.measure(measurement.engine, 1, "generic", 1, None, 20)


def test_measure_exposes_resource_regressions_and_actual_database_cpu(measurement, monkeypatch):
    measurement.connection.plans["current"] = execution_plan(groups=1000, buffers=200, spill=True)
    measurement.connection.plans["original"] = execution_plan(groups=30)
    cpu_values = iter(range(0, 100000, 1000))
    monkeypatch.setattr(profile, "postgres_cpu", lambda _container: next(cpu_values))
    report = profile.measure(measurement.engine, 30, "generic", 1, "postgres", 20)
    assert {key for key, passed in report["checks"].items() if not passed} == {
        "grouping_estimates_not_worse",
        "grouping_estimates_bounded",
        "aggregate_disk_usage_zero",
        "temporary_writes_zero",
        "no_sort_spills",
        "buffer_accesses_not_amplified",
    }
    assert report["strategies"]["current"]["samples"][0]["database_container_cpu_ms"] == 1


def test_measure_flags_runtime_regressions_beyond_both_noise_allowances(measurement, monkeypatch):
    timestamps = iter([0, 0.01, 1, 1.2, 2, 2.3, 3, 3.01])
    monkeypatch.setattr(profile.time, "perf_counter", lambda: next(timestamps))
    report = profile.measure(measurement.engine, 30, "custom", 1, None, 20)
    assert report["strategies"]["original"]["median_ms"] == pytest.approx(10)
    assert report["strategies"]["current"]["median_ms"] == pytest.approx(300)
    assert not report["checks"]["runtime_within_noise_allowance"]
    assert all(
        passed
        for name, passed in report["checks"].items()
        if name != "runtime_within_noise_allowance"
    )


def test_ingestion_owns_and_finishes_its_write_connection(monkeypatch):
    connection = Connection()
    writer = profile.Ingestion(Engine(connection), 20)
    monkeypatch.setattr(writer.stop, "wait", lambda _delay: writer.stop.set())
    with writer:
        assert writer.ready.is_set()
    assert writer.count == len(writer.samples) == 1
    assert connection.commits == 1
    assert len(connection.calls) == 2
    assert "INSERT INTO articles" in connection.calls[0][0]
    assert "UPDATE article_analysis_jobs" in connection.calls[1][0]
    assert connection.calls[1][1]["now"] == profile.NOW + timedelta(days=1)
    assert not writer.thread.is_alive()


def test_ingestion_surfaces_connection_errors_before_measurement():
    engine = SimpleNamespace(connect=lambda: (_ for _ in ()).throw(RuntimeError("writer offline")))
    writer = profile.Ingestion(engine, 20)
    with pytest.raises(AssertionError, match="writer offline"), writer:
        pytest.fail("measurement started without a writer")
    writer.thread.join(timeout=1)
    assert writer.errors == ["writer offline"]


def test_ingestion_exit_reports_errors_instead_of_silent_success():
    writer = profile.Ingestion(Engine(Connection()), 20)
    writer.thread = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    writer.errors.append("commit failed")
    with pytest.raises(AssertionError, match="commit failed"):
        writer.__exit__()
    assert writer.stop.is_set()


@pytest.fixture
def main_run(monkeypatch, disposable_environment):
    class ServerConnection(Connection):
        def exec_driver_sql(self, statement):
            super().exec_driver_sql(statement)
            return Result(
                "devfeed_token_profile_test"
                if "current_database" in statement
                else "PostgreSQL 17.11 on aarch64"
            )

    engine = Engine(ServerConnection())
    seeds = []
    monkeypatch.setattr(profile, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(profile, "seed", lambda _engine, count: seeds.append(count))
    monkeypatch.setattr(profile, "relation_sizes", lambda _connection: [{"toast_bytes": 4096}])
    monkeypatch.setattr(
        profile,
        "history_counts",
        lambda _connection: copy.deepcopy(profile.PRODUCTION_VOLUME["histories"]),
    )

    def measure(_engine, days, mode, repeats, container, rate):
        assert repeats == 1 and container is None and rate == 20
        return {
            "days": days,
            "plan_mode": mode,
            "checks": {"no_sort_spills": True},
            "strategies": {"original": {"median_ms": 20}, "current": {"median_ms": 10}},
        }

    monkeypatch.setattr(profile, "measure", measure)
    return SimpleNamespace(engine=engine, seeds=seeds)


def test_main_saves_all_scales_and_volume_checks_and_disposes_engine(main_run, tmp_path):
    profile.main(["--seed", "--articles", "100000", "300000", "--repeats", "1"])
    report = json.loads((tmp_path / profile.REPORT_NAME).read_text())
    assert report["production_volume"] == profile.PRODUCTION_VOLUME
    assert main_run.seeds == [100000, 300000]
    assert [scale["articles"] for scale in report["scales"]] == [100000, 300000]
    for scale in report["scales"]:
        assert all(scale["production_volume_checks"].values())
        assert len(scale["production_volume_checks"]) == 13
        assert {(item["days"], item["plan_mode"]) for item in scale["reports"]} == {
            (days, mode) for days in (1, 7, 30) for mode in ("custom", "generic")
        }
    assert main_run.engine.disposed


def test_main_can_profile_one_existing_small_fixture_without_seeding(main_run):
    profile.main(["--articles", "100", "--repeats", "1"])
    assert main_run.seeds == []
    assert main_run.engine.disposed


def test_main_fails_closed_on_perf_and_production_volume_gaps(main_run, monkeypatch, tmp_path):
    monkeypatch.setattr(
        profile,
        "history_counts",
        lambda _connection: {
            kind: {field: 0 for field in fields}
            for kind, fields in profile.PRODUCTION_VOLUME["histories"].items()
        },
    )
    original = profile.measure

    def failed(*args):
        report = original(*args)
        report["checks"]["no_sort_spills"] = False
        return report

    monkeypatch.setattr(profile, "measure", failed)
    with pytest.raises(SystemExit, match="fixture below production article_analysis.total_rows"):
        profile.main(["--articles", "100000", "--repeats", "1"])
    report = json.loads((tmp_path / profile.REPORT_NAME).read_text())
    assert len(report["scales"][0]["reports"]) == 6
    assert not report["scales"][0]["reports"][0]["checks"]["no_sort_spills"]
    assert main_run.engine.disposed


def test_main_disposes_engine_when_a_measurement_raises(main_run, monkeypatch):
    def fail(*_args):
        raise RuntimeError("database timed out")

    monkeypatch.setattr(profile, "measure", fail)
    with pytest.raises(RuntimeError, match="database timed out"):
        profile.main(["--articles", "100", "--repeats", "1"])
    assert main_run.engine.disposed

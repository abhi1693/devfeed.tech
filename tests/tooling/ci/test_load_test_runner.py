"""The local harness keeps report paths bounded and subprocess streams separate."""

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "load_test_runner", REPO_ROOT / "scripts/load-test.py"
)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_api_listener_disables_nagle_on_inherited_connections_and_closes():
    with runner.api_listener() as listener:
        assert listener.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        assert listener.getsockname()[0] == "127.0.0.1"
        with socket.create_connection(listener.getsockname(), timeout=2) as client:
            connection, _ = listener.accept()
            with connection:
                assert connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
                client.sendall(b"probe")
                assert connection.recv(5) == b"probe"
    assert listener.fileno() == -1


@pytest.mark.parametrize("absolute", [False, True])
def test_report_paths_support_nested_workspace_reports_and_existing_directories(
    monkeypatch, tmp_path, absolute
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "reports/performance/base-0"
    output.mkdir(parents=True)
    supplied = output if absolute else Path("reports/performance/base-0")
    assert runner.report_directory(supplied) == output


@pytest.mark.parametrize(
    "kind", ["traversal", "absolute", "sibling-prefix", "symlink", "reports-symlink"]
)
def test_output_escapes_fail_before_creating_reports_or_docker_resources(
    monkeypatch, tmp_path, capsys, kind
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("unchanged")
    monkeypatch.chdir(workspace)
    if kind == "symlink":
        (workspace / "reports").mkdir()
        (workspace / "reports/escape").symlink_to(outside, target_is_directory=True)
        supplied = "reports/escape/new"
    elif kind == "reports-symlink":
        (workspace / "reports").symlink_to(outside, target_is_directory=True)
        supplied = "reports/new"
    else:
        supplied = {
            "traversal": "reports/../../outside/new",
            "absolute": str(outside / "new"),
            "sibling-prefix": "reports-other/new",
        }[kind]
    monkeypatch.setattr(runner.sys, "argv", ["load-test.py", "--output", supplied])
    docker = Mock()
    monkeypatch.setattr(runner, "docker", docker)
    statistics = Mock()
    monkeypatch.setattr(runner, "statistics_report", statistics)

    with pytest.raises(SystemExit) as error:
        runner.main()

    assert error.value.code == 2
    assert "current directory's reports folder" in capsys.readouterr().err
    docker.assert_not_called()
    statistics.assert_not_called()
    assert {path.name for path in outside.iterdir()} == {"sentinel"}
    assert (outside / "sentinel").read_text() == "unchanged"
    assert not (workspace / "reports-other").exists()
    if kind not in {"symlink", "reports-symlink"}:
        assert not (workspace / "reports").exists()


@pytest.mark.parametrize("target_exists", [False, True])
def test_statistics_symlinks_fail_before_docker_without_touching_targets(
    monkeypatch, tmp_path, target_exists
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "reports/loadtest"
    output.mkdir(parents=True)
    target = tmp_path / "outside.json"
    if target_exists:
        target.write_text("unchanged")
    destination = output / "database-statistics.json"
    destination.symlink_to(target)
    monkeypatch.setattr(runner.sys, "argv", ["load-test.py"])
    docker = Mock()
    monkeypatch.setattr(runner, "docker", docker)

    with pytest.raises(OSError):
        runner.main()

    docker.assert_not_called()
    assert destination.is_symlink()
    if target_exists:
        assert target.read_text() == "unchanged"
    else:
        assert not target.exists()


def test_repeated_runs_replace_the_regular_statistics_report(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "reports/loadtest"
    output.mkdir(parents=True)
    for value in ([{"estimated_rows": 1000}], [{"estimated_rows": 200}]):
        with runner.statistics_report(runner.report_directory(output)) as statistics:
            json.dump(value, statistics)
        destination = output / "database-statistics.json"
        assert json.loads(destination.read_text()) == value
        assert destination.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("failing_stage", [None, "migration", "seed"])
def test_database_setup_keeps_statistics_json_separate_from_logs_and_propagates_failure(
    monkeypatch, tmp_path, failing_stage
):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "reports/custom run"
    output.mkdir(parents=True)
    trace = tmp_path / "commands.jsonl"
    application_python = tmp_path / "application-python"
    application_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['COMMAND_TRACE'], 'a') as trace:\n"
        "    trace.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == '-m':\n"
        "    print('migration stdout')\n"
        "    print('migration stderr', file=sys.stderr)\n"
        "    stage = 'migration'\n"
        "else:\n"
        "    assert len(sys.argv) == 3 and sys.argv[2] == '1000'\n"
        "    print(json.dumps([{'table_name': 'articles', 'estimated_rows': 1000}]))\n"
        "    print('Seeded 1000 published articles, 20 sources and 20 topics', file=sys.stderr)\n"
        "    stage = 'seed'\n"
        "sys.exit(7 if os.environ.get('FAIL_STAGE') == stage else 0)\n"
    )
    application_python.chmod(0o755)
    application_root = tmp_path / "baseline"
    args = SimpleNamespace(
        app_python=application_python, app_root=application_root, rows=1000, output=output
    )
    env = {**os.environ, "COMMAND_TRACE": str(trace), "FAIL_STAGE": failing_stage or ""}
    with runner.statistics_report(output) as statistics:
        if failing_stage:
            with pytest.raises(subprocess.CalledProcessError) as error:
                runner.prepare_database(args, env, tmp_path, statistics)
            assert error.value.returncode == 7
        else:
            runner.prepare_database(args, env, tmp_path, statistics)

    commands = [json.loads(line) for line in trace.read_text().splitlines()]
    assert commands[0] == [
        "-m",
        "alembic",
        "-c",
        str(application_root / "alembic.ini"),
        "upgrade",
        "head",
    ]
    logs = (output / "setup.log").read_text()
    assert "migration stdout" in logs and "migration stderr" in logs
    if failing_stage == "migration":
        assert len(commands) == 1
        assert not (output / "database-statistics.json").read_text()
    else:
        assert commands[1] == [str(runner.ROOT / "loadtests/seed.py"), "1000"]
        assert json.loads((output / "database-statistics.json").read_text()) == [
            {"table_name": "articles", "estimated_rows": 1000}
        ]
        assert "Seeded 1000 published articles, 20 sources and 20 topics" in logs
        assert "estimated_rows" not in logs


def statement(queryid, calls, elapsed, reads=0):
    counters = dict.fromkeys(runner.STATEMENT_COUNTERS, 0)
    return {
        **counters,
        "queryid": queryid,
        "query": f"SELECT fixture_{queryid}",
        "calls": calls,
        "total_exec_time": elapsed,
        "shared_blks_read": reads,
    }


def test_statement_deltas_remove_warmup_and_keep_new_queries_sorted_by_database_cost():
    before = [statement("feed", 40, 400, 5), statement("warmup_only", 4, 30)]
    after = [
        statement("feed", 50, 600, 7),
        statement("warmup_only", 4, 30),
        statement("article", 20, 40, 1),
    ]

    measured = runner.statement_delta(before, after)

    assert [row["queryid"] for row in measured] == ["feed", "article"]
    assert measured[0]["calls"] == 10
    assert measured[0]["total_exec_time"] == 200
    assert measured[0]["shared_blks_read"] == 2
    assert measured[1]["calls"] == 20
    assert measured[1]["total_exec_time"] == 40


def test_statement_counter_reset_cannot_report_an_apparent_improvement():
    with pytest.raises(ValueError, match="counters reset"):
        runner.statement_delta([statement("feed", 10, 100)], [statement("feed", 2, 20)])


def test_process_cpu_parses_names_with_spaces_and_parentheses(monkeypatch, tmp_path):
    process = tmp_path / "123"
    process.mkdir()
    fields = ["0"] * 22
    fields[0], fields[11], fields[12], fields[21] = "S", "120", "30", "8"
    (process / "stat").write_text("123 (worker (test) process) " + " ".join(fields))
    monkeypatch.setattr(runner.os, "sysconf", lambda key: 100 if key == "SC_CLK_TCK" else 4096)

    assert runner.process_statistics(123, tmp_path) == {"cpu_seconds": 1.5, "rss_bytes": 32768}


def test_finished_load_process_can_disappear_before_its_cpu_sample(monkeypatch):
    monkeypatch.setattr(runner, "process_statistics", Mock(side_effect=FileNotFoundError))
    assert runner.optional_process_statistics(123) is None
    assert runner.optional_process_statistics(None) is None


def test_runtime_snapshot_keeps_live_pool_waits_and_top_level_sql_cost(monkeypatch):
    queries = [statement("feed", 10, 20)]
    activity = [{"state": "active", "wait_event_type": "Lock", "connections": 2}]
    database = Mock()
    database.execute.side_effect = [
        Mock(),
        Mock(fetchall=Mock(return_value=queries)),
        Mock(fetchall=Mock(return_value=activity)),
        Mock(fetchone=Mock(return_value={"numbackends": 6, "blks_hit": 123})),
    ]
    pools = [{"database": "devfeed_load_test", "cl_waiting": 3, "sv_active": 5}]
    pooler = Mock()
    pooler.execute.return_value.fetchall.return_value = pools
    monkeypatch.setattr(runner, "docker", Mock(return_value="usage_usec 240000\nnr_throttled 2"))
    monkeypatch.setattr(runner, "process_statistics", lambda pid: {"cpu_seconds": pid})

    sample = runner.runtime_snapshot(
        database, pooler, "own-postgres", 12, 13, {"phase": "measurement"}
    )

    assert sample["api_process"]["cpu_seconds"] == 12
    assert sample["load_process"]["cpu_seconds"] == 13
    assert sample["postgres_cgroup"] == {"usage_usec": 240000, "nr_throttled": 2}
    assert sample["pooler"] == pools
    assert sample["activity"] == activity
    assert sample["database"]["numbackends"] == 6
    assert sample["queries"] == queries
    sql = database.execute.call_args_list[1].args[0]
    assert "AND toplevel" in sql and "query NOT LIKE '%performance diagnostics%'" in sql
    pooler.execute.assert_called_once_with("SHOW POOLS", prepare=False)


def measurement_sample(phase, clock, *, elapsed=0, calls=0):
    return {
        "recorded_at": f"observed-{clock}",
        "monotonic": clock,
        "phase": dict(phase),
        "api_process": {"cpu_seconds": elapsed},
        "postgres_cgroup": {"usage_usec": elapsed * 1_000_000},
        "queries": [statement("feed", calls, elapsed * 10)],
    }


def test_measurement_records_live_samples_and_excludes_warmup_cost(monkeypatch, tmp_path):
    phase_file, output = tmp_path / "measurement.json", tmp_path / "runtime.json"
    clock = [0.0]
    process = Mock(pid=123, returncode=0)
    process.poll.side_effect = lambda: 0 if clock[0] >= 12 else None
    monkeypatch.setattr(runner.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    measured = {"phase": "measurement", "started_monotonic": 1}

    def advance(seconds):
        clock[0] += seconds
        if clock[0] >= 12:
            phase_file.write_text(
                json.dumps({**measured, "phase": "finished", "finished_monotonic": 12})
            )
        elif clock[0] >= 1:
            phase_file.write_text(json.dumps(measured))

    monkeypatch.setattr(runner.time, "sleep", advance)
    result = runner.run_measured_load(
        ["locust"],
        env={},
        cwd=tmp_path,
        timeout=30,
        phase_file=phase_file,
        output=output,
        capture=lambda pid, phase: measurement_sample(
            phase, clock[0], elapsed=round(clock[0], 1), calls=round(clock[0] * 10)
        ),
    )

    report = json.loads(output.read_text())
    assert result == 0
    assert [row["phase"]["phase"] for row in report["samples"]] == [
        "starting",
        "measurement",
        "measurement",
        "measurement",
        "finished",
    ]
    assert report["measurement"]["api_cpu_seconds"] == pytest.approx(11)
    assert report["measurement"]["postgres_cpu_seconds"] == pytest.approx(11)
    assert report["measurement"]["queries"][0]["calls"] == 110
    assert 0 <= report["measurement"]["start_observation_lag_seconds"] < 0.11
    assert 0 <= report["measurement"]["end_observation_lag_seconds"] < 0.11
    process.terminate.assert_not_called()


@pytest.mark.parametrize("failure", ["watchdog", "telemetry", "incomplete"])
def test_load_failures_retain_partial_diagnostics_and_stop_owned_process(
    monkeypatch, tmp_path, failure
):
    clock = [0.0]
    process = Mock(pid=123, returncode=7)
    process.poll.side_effect = lambda: 7 if failure == "incomplete" else None
    monkeypatch.setattr(runner.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        runner.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    output = tmp_path / "runtime.json"

    def capture(pid, phase):
        if failure == "telemetry":
            raise OSError("database diagnostics unavailable")
        return measurement_sample(phase, clock[0])

    error = {"watchdog": subprocess.TimeoutExpired, "telemetry": OSError, "incomplete": ValueError}[
        failure
    ]
    with pytest.raises(error):
        runner.run_measured_load(
            ["locust"],
            env={},
            cwd=tmp_path,
            timeout=0.2,
            phase_file=tmp_path / "measurement.json",
            output=output,
            capture=capture,
        )

    report = json.loads(output.read_text())
    assert error.__name__ in report["error"]
    if failure == "incomplete":
        assert report["exit_code"] == 7
        process.terminate.assert_not_called()
    else:
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
    if failure != "telemetry":
        assert report["samples"][0]["phase"]["phase"] == "starting"


@pytest.mark.parametrize("outcome", ["success", "retained", "failed"])
def test_disposable_run_wires_equal_measurement_and_diagnostics_and_cleans_only_owned_resources(
    monkeypatch, tmp_path, outcome
):
    import psycopg

    # Backend unit CI deliberately omits the optional load-generator environment.
    loadtest_version = Mock(return_value="isolated-locust-version")
    monkeypatch.setattr(runner, "version", loadtest_version)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVFEED_DATABASE_URL", "postgresql://unrelated/production")
    monkeypatch.setenv("LOCUST_HOST", "https://unrelated.example")
    generated_password = "e" * 64
    generate_password = Mock(return_value=generated_password)
    monkeypatch.setattr(runner.secrets, "token_hex", generate_password)
    output = tmp_path / "reports/paired/head-0"
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "load-test.py",
            "--users",
            "16",
            "--spawn-rate",
            "4",
            "--seconds",
            "60",
            "--warmup-seconds",
            "15",
            "--journey-seed",
            "294",
            "--output",
            str(output),
        ],
    )
    containers = iter(["owned-postgres", "owned-redis", "owned-pooler"])

    def docker(*args):
        if args[:2] == ("network", "create"):
            return "owned-network"
        if args[0] == "run":
            return next(containers)
        if args[0] == "port":
            return {
                "5432/tcp": "127.0.0.1:55494",
                "6379/tcp": "127.0.0.1:56495",
                "6432/tcp": "127.0.0.1:56494",
            }[args[-1]]
        if args[:2] == ("exec", "owned-redis"):
            return "PONG"
        if args[0] == "inspect":
            return f"sha256:{args[-1]}"
        raise AssertionError(args)

    docker_call = Mock(side_effect=docker)
    monkeypatch.setattr(runner, "docker", docker_call)
    monkeypatch.setattr(runner, "ready", lambda check, description: check())
    monkeypatch.setattr(
        runner.urllib.request, "urlopen", Mock(return_value=SimpleNamespace(status=200))
    )
    commands = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runner.subprocess, "run", commands)
    monkeypatch.setattr(
        runner.subprocess,
        "check_output",
        lambda command, **kwargs: "" if command[1] == "status" else "exact-head-sha\n",
    )
    api = Mock(pid=4321)
    if outcome == "failed":
        api.wait.side_effect = [subprocess.TimeoutExpired("api", 15), None]
    spawn_api = Mock(return_value=api)
    monkeypatch.setattr(runner.subprocess, "Popen", spawn_api)
    database, pooler = Mock(), Mock()
    database.execute.return_value.fetchone.return_value = {"version": "PostgreSQL 18 fixture"}
    pools = [
        {
            "database": "devfeed_load_test",
            "cl_active": int(outcome == "retained"),
            "cl_waiting": 0,
            "sv_active": 0,
        }
    ]
    pooler.execute.return_value.fetchall.return_value = pools
    connect = Mock(side_effect=[database, pooler])
    monkeypatch.setattr(psycopg, "connect", connect)
    setup_environments = []
    prepare = Mock(side_effect=lambda args, env, *rest: setup_environments.append(dict(env)))
    monkeypatch.setattr(runner, "prepare_database", prepare)
    snapshot = Mock(return_value={"live": "diagnostics"})
    monkeypatch.setattr(runner, "runtime_snapshot", snapshot)

    def measure(command, **kwargs):
        assert kwargs["capture"](9876, {"phase": "measurement"}) == {"live": "diagnostics"}
        assert "--reset-stats" not in command
        for option, value in {
            "--users": "16",
            "--spawn-rate": "4.0",
            "--measurement-seconds": "60",
            "--warmup-seconds": "15",
            "--journey-seed": "294",
            "--phase-file": str(output / "measurement.json"),
            "--run-time": "199",
        }.items():
            assert command[command.index(option) + 1] == value
        assert kwargs["timeout"] == 259
        assert kwargs["env"]["DEVFEED_DATABASE_URL"].startswith(
            f"postgresql+psycopg://load:{generated_password}@127.0.0.1:56494/"
        )
        assert kwargs["env"]["DEVFEED_REDIS_URL"] == "redis://127.0.0.1:56495/15"
        assert "LOCUST_HOST" not in kwargs["env"]
        assert f"password={generated_password}" in (kwargs["cwd"] / "pgbouncer.ini").read_text()
        assert (kwargs["cwd"] / "users.txt").read_text() == f'"load" "{generated_password}"\n'
        kwargs["output"].write_text(json.dumps({"samples": ["retained"], "error": outcome}))
        if outcome == "failed":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return 0

    monkeypatch.setattr(runner, "run_measured_load", measure)
    if outcome == "failed":
        with pytest.raises(subprocess.TimeoutExpired):
            runner.main()
        api.kill.assert_called_once()
    else:
        assert runner.main() == int(outcome == "retained")
        assert json.loads((output / "pooler-after.json").read_text()) == pools
        pooler.execute.assert_called_once_with("SHOW POOLS", prepare=False)
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["protocol_version"] == "reader-load-v2"
    assert metadata["locust_version"] == "isolated-locust-version"
    loadtest_version.assert_called_once_with("locust")
    assert (metadata["journey_seed"], metadata["warmup_seconds"], metadata["seconds"]) == (
        294,
        15,
        60,
    )
    assert metadata["commit"] == "exact-head-sha"
    assert metadata["postgres_version"] == "PostgreSQL 18 fixture"
    assert generated_password not in (output / "metadata.json").read_text()
    assert metadata["container_images"] == {
        "postgres": "sha256:owned-postgres",
        "redis": "sha256:owned-redis",
        "pooler": "sha256:owned-pooler",
    }
    postgres_command = next(
        call.args
        for call in docker_call.call_args_list
        if call.args[:1] == ("run",) and "postgres:18-alpine" in call.args
    )
    assert "shared_preload_libraries=pg_stat_statements" in postgres_command
    assert "pg_stat_statements.track=top" in postgres_command
    assert f"POSTGRES_PASSWORD={generated_password}" in postgres_command
    generate_password.assert_called_once_with(32)
    assert all(call.kwargs["password"] == generated_password for call in connect.call_args_list)
    database.execute.assert_any_call("CREATE EXTENSION pg_stat_statements")
    assert setup_environments[0]["DEVFEED_DATABASE_URL"].startswith(
        f"postgresql+psycopg://load:{generated_password}@127.0.0.1:55494/"
    )
    snapshot.assert_called_once_with(
        database, pooler, "owned-postgres", 4321, 9876, {"phase": "measurement"}
    )
    assert json.loads((output / "runtime-diagnostics.json").read_text())["samples"] == ["retained"]
    database.close.assert_called_once()
    pooler.close.assert_called_once()
    api.terminate.assert_called_once()
    cleanup = [
        call.args[0]
        for call in commands.call_args_list
        if call.args[0][:3] == ["docker", "rm", "-f"]
        or call.args[0][:3] == ["docker", "network", "rm"]
    ]
    assert cleanup == [
        ["docker", "rm", "-f", "owned-pooler"],
        ["docker", "rm", "-f", "owned-redis"],
        ["docker", "rm", "-f", "owned-postgres"],
        ["docker", "network", "rm", "owned-network"],
    ]

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

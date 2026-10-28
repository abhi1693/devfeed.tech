"""Dispatch measurements own their targets and verify real database/queue delivery."""

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.support.paths import REPO_ROOT


@pytest.fixture
def benchmark(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    return importlib.import_module("worker_dispatch_benchmark")


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--jobs", "0"], "Require 1..100000 jobs"),
        (["--jobs", "100001"], "Require 1..100000 jobs"),
        (["--repetitions", "0"], "1..10 repetitions"),
        (["--repetitions", "11"], "1..10 repetitions"),
        (["--batch", "0"], "Each batch must be positive"),
        (["--batch", "-1"], "Each batch must be positive"),
        (["--jobs", "2", "--batch", "3"], "no larger than the fixture"),
    ],
)
def test_invalid_limits_are_rejected_before_creating_services(
    benchmark, monkeypatch, capsys, arguments, message
):
    start = Mock(side_effect=AssertionError("Invalid arguments started disposable services"))
    monkeypatch.setattr(benchmark, "disposable_services", start)
    monkeypatch.setattr(sys, "argv", ["worker-dispatch-benchmark", *arguments])

    with pytest.raises(SystemExit) as error:
        benchmark.main()

    assert error.value.code == 2
    assert message in capsys.readouterr().err
    start.assert_not_called()


@pytest.mark.integration
def test_cli_measures_real_dispatch_with_owned_targets_and_restores_environment(
    benchmark, monkeypatch, tmp_path
):
    from devfeed_core.config import get_settings
    from devfeed_core.db import get_engine

    def clear_database_cache():
        if get_engine.cache_info().currsize:
            get_engine().dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()

    clear_database_cache()
    inherited_database = "postgresql+psycopg://never@database.invalid/never_use_test"
    monkeypatch.setenv("DEVFEED_DATABASE_URL", inherited_database)
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://redis.invalid/0")
    monkeypatch.setenv("DEVFEED_PRIVATE_BENCHMARK_SETTING", "ignore inherited settings")
    output = tmp_path / "reports" / "dispatch.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker-dispatch-benchmark",
            "--jobs",
            "1001",
            "--batch",
            "1",
            "3",
            "--repetitions",
            "2",
            "--output",
            str(output),
        ],
    )
    original_directory = Path.cwd()
    directories = []
    profile = benchmark.profile

    def profile_owned_target(arguments):
        assert os.environ["DEVFEED_DATABASE_URL"].endswith("/devfeed_test")
        assert os.environ["DEVFEED_DATABASE_URL"] != inherited_database
        assert os.environ["DEVFEED_REDIS_URL"].endswith("/15")
        assert "DEVFEED_PRIVATE_BENCHMARK_SETTING" not in os.environ
        assert os.environ["OTEL_SDK_DISABLED"] == "true"
        directories.append(Path.cwd())
        assert directories[-1] != original_directory
        return profile(arguments)

    monkeypatch.setattr(benchmark, "profile", profile_owned_target)
    try:
        benchmark.main()
    finally:
        clear_database_cache()

    assert Path.cwd() == original_directory
    assert directories and not directories[0].exists()
    assert os.environ["DEVFEED_DATABASE_URL"] == inherited_database
    assert os.environ["DEVFEED_PRIVATE_BENCHMARK_SETTING"] == "ignore inherited settings"
    report = json.loads(output.read_text())
    assert report["fixture_jobs"] == 1001
    assert report["repetitions"] == 2
    assert len(report["revision"]) == 40
    assert len(report["source_sha256"]) == 64
    assert report["architecture"]
    assert report["peak_python_process_rss_bytes"] > 0
    assert set(report["versions"]) == {"sqlalchemy", "psycopg", "redis", "rq"}
    assert {(row["mode"], row["batch"]) for row in report["results"]} == {
        ("direct", 1),
        ("direct", 3),
        ("lanes", 1),
        ("lanes", 3),
    }
    for row in report["results"]:
        assert len(row["samples"]) == 2
        assert row["median_ms"] > 0
        for sample in row["samples"]:
            assert sample["selects"] > 0
            assert sample["update_batches"] == 1
            assert sample["commits"] == 1
            assert sample["milliseconds"] > 0
            assert sample["python_cpu_ms"] >= 0

import io
import json
import uuid

import pytest
from devfeed_cli import commands
from devfeed_cli.main import run
from devfeed_core.config import get_settings
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["sources", "add"],
        ["worker"],
        ["scheduler"],
        ["categories", "update"],
        ["db", "upgrade"],
        ["articles", "detect-languages"],
        ["articles", "classify"],
        ["articles", "analyze"],
        ["articles", "publish"],
        ["topics", "add"],
        ["topics", "accept"],
    ],
)
def test_help_works_without_connection_configuration(arguments, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    with pytest.raises(SystemExit) as caught:
        run([*arguments, "--help"])
    assert caught.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_commands_require_explicit_connection_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    get_settings.cache_clear()
    assert run(["sources", "list"]) == 2
    error = capsys.readouterr().err
    assert "database_url" in error and "redis_url" in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    "arguments",
    [
        [
            "sources",
            "add",
            "https://example.com/rss",
            "--type",
            "publisher",
            "--poll-interval",
            "1",
        ],
        ["sources", "show", "not-a-uuid"],
        ["sources", "list", "--limit", "0"],
        ["jobs", "list", "--status", "unknown"],
        ["worker", "--max-jobs", "0"],
        ["categories", "add", "--name", "Missing slug"],
    ],
)
def test_invalid_arguments_are_usage_errors(arguments):
    with pytest.raises(SystemExit) as caught:
        run(arguments)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/rss", "file:///rss", "https://user:secret@example.com/rss"]
)
def test_invalid_feed_urls_never_open_database(url, monkeypatch, capsys):
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    assert run(["sources", "add", url, "--type", "publisher"]) == 2
    assert "secret" not in capsys.readouterr().err


def test_worker_and_scheduler_commands_delegate_to_runtime(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(commands.worker, "run", lambda **kwargs: calls.append(kwargs))
    assert run(["worker", "--burst", "--name", "test-worker", "--max-jobs", "2"]) == 0
    assert calls == [{"burst": True, "name": "test-worker", "max_jobs": 2}]
    assert run(["worker", "--queue", "analysis", "--burst"]) == 0
    assert calls[-1] == {"burst": True, "name": None, "max_jobs": None, "queue_name": "analysis"}
    monkeypatch.setattr(commands.scheduler, "tick", lambda: {"dispatched": 3})
    assert run(["scheduler", "--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {"dispatched": 3}
    monkeypatch.setattr(commands.scheduler, "run", lambda: calls.append("scheduler"))
    assert run(["scheduler"]) == 0
    assert calls[-1] == "scheduler"


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("redis://secret@host"),
        OperationalError("secret", {}, Exception("secret")),
    ],
)
def test_connection_errors_have_nonzero_exit_and_no_credentials(error, monkeypatch, capsys):
    def failure():
        raise error

    monkeypatch.setattr(commands.scheduler, "tick", failure)
    assert run(["scheduler", "--once"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "secret" not in output.err and "Traceback" not in output.err


def test_empty_or_oversized_import_is_rejected_before_database(monkeypatch, capsys):
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    for content in ("# comment\n\n", "x" * (commands.MAX_IMPORT_BYTES + 1)):
        monkeypatch.setattr("sys.stdin", io.StringIO(content))
        assert run(["sources", "import", "-", "--type", "publisher"]) == 2
        assert capsys.readouterr().out == ""


def test_missing_import_file_is_a_clean_error(tmp_path, capsys):
    assert run(["sources", "import", str(tmp_path / "missing.txt"), "--type", "publisher"]) == 1
    assert "Unable to read" in capsys.readouterr().err


def test_empty_update_is_rejected_before_database(monkeypatch, capsys):
    monkeypatch.setattr(commands, "session_factory", lambda: pytest.fail("Opened database"))
    for resource in ("sources", "categories", "tags"):
        assert run([resource, "update", str(uuid.uuid4())]) == 2
        assert "Specify at least one" in capsys.readouterr().err

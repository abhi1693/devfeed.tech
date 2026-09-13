import io
import json
import uuid
from types import SimpleNamespace

import pytest
from devfeed_cli import commands, editorial
from devfeed_cli.main import run
from devfeed_core.config import get_settings
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError


def test_tag_backfill_accepts_a_bounded_dry_run_and_cursor(monkeypatch, capsys):
    from devfeed_cli import articles

    captured = []
    cursor, source_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(
        articles, "restore_tags", lambda args: captured.append(args) or {"links_saved": 0}
    )
    assert (
        run(
            [
                "articles",
                "backfill-tags",
                "--limit",
                "25",
                "--after",
                str(cursor),
                "--source-id",
                str(source_id),
                "--dry-run",
            ]
        )
        == 0
    )
    assert captured[0].limit == 25 and captured[0].dry_run is True
    assert captured[0].after == cursor and captured[0].source_id == source_id
    assert json.loads(capsys.readouterr().out) == {"links_saved": 0}


@pytest.mark.parametrize("dispatch", [False, True])
def test_analysis_backfill_force_is_independent_of_dispatch(monkeypatch, capsys, dispatch):
    captured = []
    monkeypatch.setattr(
        editorial, "analysis_backfill", lambda args: captured.append(args) or {"queued": 0}
    )
    arguments = ["articles", "analysis-backfill", "--limit", "100", "--force"]
    if dispatch:
        arguments.append("--dispatch")
    assert run(arguments) == 0
    assert captured[0].force is True and captured[0].dispatch is dispatch
    assert captured[0].limit == 100
    assert json.loads(capsys.readouterr().out) == {"queued": 0}


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["sources", "add"],
        ["worker"],
        ["scheduler"],
        ["topics", "update"],
        ["db", "upgrade"],
        ["articles", "detect-languages"],
        ["articles", "backfill-tags"],
        ["articles", "classify"],
        ["articles", "analyze"],
        ["articles", "publish"],
        ["topics", "add"],
    ],
)
def test_help_works_without_connection_configuration(arguments, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    with pytest.raises(SystemExit) as caught:
        run([*arguments, "--help"])
    assert caught.value.code == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_commands_require_explicit_connection_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    get_settings.cache_clear()
    assert run(["sources", "list"]) == 2
    error = capsys.readouterr().err
    assert "database_url" in error and "redis_url" in error
    assert "Traceback" not in error


def test_article_topic_accept_command_has_been_removed(capsys):
    with pytest.raises(SystemExit) as caught:
        run(["topics", "accept", str(uuid.uuid4()), "--slug", "new-topic"])
    assert caught.value.code == 2
    assert "No such command" in capsys.readouterr().err


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
    assert run(["worker", "--queue", "background", "--burst"]) == 0
    assert calls[-1]["queue_name"] == "background"
    monkeypatch.setattr(commands.scheduler, "tick", lambda: {"dispatched": 3})
    assert run(["scheduler", "--once"]) == 0
    assert json.loads(capsys.readouterr().out) == {"dispatched": 3}
    monkeypatch.setattr(commands.scheduler, "run", lambda: calls.append("scheduler"))
    assert run(["scheduler"]) == 0
    assert calls[-1] == "scheduler"


@pytest.mark.parametrize("duplicate_name", [False, True])
def test_worker_startup_errors_are_actionable_without_exposing_configuration(
    monkeypatch, capsys, duplicate_name
):
    closed = []
    queue = SimpleNamespace(connection=SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(commands.worker, "get_queue", lambda *args: queue)

    def fail(**kwargs):
        if duplicate_name:
            raise ValueError("There exists an active worker named 'test-worker' already")
        raise ValueError("Invalid connection redis://user:secret@host")

    monkeypatch.setattr(
        commands.worker,
        "Worker",
        lambda *args, **kwargs: SimpleNamespace(name="test-worker", work=fail),
    )
    assert run(["worker", "--name", "test-worker"]) == 2
    output = capsys.readouterr()
    expected = (
        "An RQ worker with this name is already registered. Choose a unique --name"
        if duplicate_name
        else "Invalid command input or connection configuration."
    )
    assert expected in output.err
    assert "secret" not in output.err + output.out
    assert closed == [True] * 4  # All four background queue connections close.


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
    for resource in ("sources", "tags"):
        assert run([resource, "update", str(uuid.uuid4())]) == 2
        assert "Specify at least one" in capsys.readouterr().err

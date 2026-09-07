"""Typer command contracts: no database, Redis, workers or migrations are run."""

import json
from pathlib import Path
from uuid import UUID

import pytest
import typer
from devfeed_cli import commands, runtime
from devfeed_cli.main import app
from typer.main import get_command
from typer.testing import CliRunner

runner = CliRunner()
ID = "26f737ad-1a5c-4c29-88da-34a702b89320"
OTHER = "3ae77fc2-9bf9-459b-a2e9-7edc5f78335a"
GROUPS = {
    "sources": [
        "add",
        "import",
        "list",
        "show",
        "update",
        "fetch",
        "approve",
        "reject",
        "review-history",
        "enrich",
        "enrichment-jobs",
        "enrichment-dispatch",
    ],
    "articles": [
        "detect-languages",
        "enrich",
        "retry",
        "show",
        "dispatch",
        "backfill",
        "jobs",
        "approve",
        "reject",
        "publish",
        "unpublish",
        "inspect",
        "classify",
        "list",
        "review-history",
        "analyze",
        "analysis-retry",
        "analyses",
        "analysis-dispatch",
        "analysis-backfill",
    ],
    "images": ["fetch", "retry", "show", "dispatch", "backfill", "jobs"],
    "jobs": ["list", "show", "retry", "dispatch"],
    "topics": ["list", "add", "update", "relate", "accept"],
    "tags": ["list", "add", "update"],
    "cache": ["clear"],
    "db": ["upgrade", "check", "current"],
}
PATHS = [[name] for name in ("worker", "scheduler", "status")] + [
    [group, command] for group, names in GROUPS.items() for command in names
]


@pytest.fixture
def operations(monkeypatch):
    calls = []

    def execute(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(runtime, "execute", execute)
    return calls


def test_typer_tree_preserves_every_command():
    assert isinstance(app, typer.Typer)
    command = get_command(app)
    assert set(command.commands) == set(GROUPS) | {"worker", "scheduler", "status"}
    for name, subcommands in GROUPS.items():
        assert set(command.commands[name].commands) == set(subcommands)
    assert app.pretty_exceptions_enable is False
    assert app.pretty_exceptions_show_locals is False


@pytest.mark.parametrize("path", [[], *[[name] for name in GROUPS], *PATHS])
def test_every_help_path_is_offline(path, operations, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEVFEED_DATABASE_URL")
    monkeypatch.delenv("DEVFEED_REDIS_URL")
    monkeypatch.setattr(runtime, "get_settings", lambda: pytest.fail("Settings during help"))
    result = runner.invoke(app, [*path, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.stdout and not operations
    assert result.stderr == ""


@pytest.mark.parametrize("path", PATHS)
def test_every_command_routes_to_an_operation_with_typed_arguments(path, operations):
    command, action = path[0], path[-1]
    args = list(path)
    if command == "sources" and action == "add":
        args += ["https://example.com/rss", "--type", "publisher"]
    elif command == "sources" and action == "import":
        args += ["-", "--type", "aggregator"]
    elif command in {"tags"} and action == "add":
        args += ["--name", "Testing", "--slug", "testing"]
    elif command == "topics" and action == "add":
        args += ["--file", "topic.json"]
    elif command == "topics" and action == "relate":
        args += [ID, OTHER, "--relation", "uses_language"]
    elif command == "topics" and action == "accept":
        args += [ID, "--slug", "testing"]
    elif action in {
        "update",
        "show",
        "fetch",
        "enrich",
        "retry",
        "dispatch",
        "enrichment-dispatch",
        "approve",
        "reject",
        "publish",
        "unpublish",
        "inspect",
        "classify",
        "review-history",
        "analyze",
        "analysis-retry",
        "analysis-dispatch",
    }:
        args += [ID]
        if action == "reject":
            args += ["--reason", "Not relevant"]
        if action == "classify" or (command == "topics" and action == "update"):
            args += ["--file", "input.json"]
        elif action == "update":
            args += ["--name", "Updated"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(operations) == 1
    call = operations[0]
    assert call.command == command
    assert call.action == (action if len(path) == 2 else None)
    for name in ("id", "analysis_id", "related_id"):
        if hasattr(call, name):
            assert isinstance(getattr(call, name), UUID)


@pytest.mark.parametrize(
    "command,flags,expected",
    [
        ("sources", ["--name", "Updated"], {"name": "Updated"}),
        ("sources", ["--clear-description"], {"description": None}),
        ("sources", ["--description", ""], {"description": ""}),
        ("sources", ["--enable"], {"enabled": True}),
        ("sources", ["--disable"], {"enabled": False}),
        ("sources", ["--poll-interval", "600"], {"poll_interval_seconds": 600}),
        ("tags", ["--alias", "one", "--alias", "two"], {"aliases": ["one", "two"]}),
        (
            "tags",
            ["--clear-aliases", "--clear-topic"],
            {"aliases": [], "topic_id": None},
        ),
    ],
)
def test_patch_commands_distinguish_omitted_and_cleared_fields(
    command, flags, expected, operations
):
    result = runner.invoke(app, [command, "update", ID, *flags])
    assert result.exit_code == 0, result.output
    args = vars(operations[0])
    assert {
        key: value
        for key, value in args.items()
        if key not in {"execute", "command", "action", "id"}
    } == expected


@pytest.mark.parametrize(
    "arguments",
    [
        ["sources", "list", "--enabled", "--disabled"],
        ["sources", "update", ID, "--enable", "--disable"],
        ["sources", "update", ID, "--description", "x", "--clear-description"],
        ["sources", "update", ID, "--language", "en", "--clear-language"],
        ["sources", "update", ID, "--website-url", "https://example.com", "--clear-website-url"],
        ["tags", "update", ID, "--alias", "x", "--clear-aliases"],
    ],
)
def test_conflicting_flags_fail_before_operations(arguments, operations):
    start = 2 if arguments[1] == "list" else 3
    flags = arguments[start:]
    # Move the final boolean flag before the value option, preserving its value.
    reversed_flags = [*arguments[:start], flags[-1], *flags[:-1]]
    for args in (arguments, reversed_flags):
        result = runner.invoke(app, args)
        assert result.exit_code == 2
        assert "Cannot combine" in result.stderr
        assert not operations


@pytest.mark.parametrize("kind", ["logo-url", "image-url"])
def test_conflict_does_not_echo_sensitive_values(kind, operations):
    result = runner.invoke(
        app,
        [
            "sources",
            "update",
            ID,
            f"--{kind}",
            "https://user:private-secret@example.com",
            f"--clear-{kind}",
        ],
    )
    assert result.exit_code == 2 and "private-secret" not in result.output and not operations


@pytest.mark.parametrize(
    "arguments",
    [
        ["worker", "--queue", "unknown"],
        ["sources", "add", "https://example.com/rss"],
        ["sources", "list", "--status", "bad"],
        ["sources", "list", "--offset", "-1"],
        ["sources", "update", ID, "--poll-interval", "604801"],
        ["articles", "publish", ID, "--revision", "-1"],
        ["articles", "list", "--publication-status", "pending"],
        ["articles", "analysis-backfill", "--after", "bad-id"],
        ["images", "jobs", "--limit", "501"],
        ["topics", "relate", ID, OTHER, "--relation", "unsupported"],
        ["sources", "reject", ID],
        ["articles", "reject", ID],
        ["db", "upgrade", "--not-an-option"],
    ],
)
def test_typed_validation_rejects_before_operation(arguments, operations):
    result = runner.invoke(app, arguments)
    assert result.exit_code == 2 and not operations


def test_paths_are_typed_and_stdin_marker_remains_a_string(operations):
    result = runner.invoke(app, ["db", "current", "--config", "nested/alembic.ini"])
    assert result.exit_code == 0 and operations[-1].config == Path("nested/alembic.ini")
    result = runner.invoke(app, ["sources", "import", "-", "--type", "publisher"])
    assert result.exit_code == 0 and operations[-1].file == "-"


def test_actual_entrypoint_preserves_json_stdout_and_sanitizes_failures(monkeypatch):
    monkeypatch.setattr(commands, "source_list", lambda _: [{"id": ID}])
    result = runner.invoke(app, ["sources", "list"])
    assert result.exit_code == 0 and json.loads(result.stdout) == [{"id": ID}]

    def crash(_):
        private_variable = "do-not-print-local-variables"
        raise RuntimeError(private_variable)

    monkeypatch.setattr(commands, "source_list", crash)
    result = runner.invoke(app, ["sources", "list"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "do-not-print-local-variables" not in result.stderr
    assert "Traceback" not in result.stderr and "Unexpected command failure" in result.stderr


def test_keyboard_interrupt_keeps_exit_130(monkeypatch):
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(commands, "run_worker", interrupt)
    result = runner.invoke(app, ["worker"])
    assert result.exit_code == 130


def test_completion_script_generation_is_offline_and_does_not_install(operations, monkeypatch):
    from typer import completion

    monkeypatch.setattr(completion, "_get_shell_name", lambda: "zsh")
    monkeypatch.setattr(runtime, "get_settings", lambda: pytest.fail("Settings during completion"))
    result = runner.invoke(app, ["--show-completion"])
    assert result.exit_code == 0, result.output
    assert "_DEVFEED_COMPLETE" in result.stdout and not operations

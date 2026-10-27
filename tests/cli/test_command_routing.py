"""CLI routing, validation, offline help and failures preserve typed operator intent."""

import json
import re
import runpy
import uuid
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
import typer
from alembic.util.exc import CommandError
from devfeed_cli import commands, discovery_commands, runtime
from devfeed_cli.main import app
from devfeed_core import db, topic_decision_budget
from devfeed_core.cache import CacheUnavailable
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.schemas import SourceDecision
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from typer.main import get_command
from typer.testing import CliRunner

from tests.admin.auth.test_auth import oidc_app as oidc_app
from tests.admin.taxonomy.test_taxonomy_command_adapters_unit import tag
from tests.cli.test_editorial_commands import command as command
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration

runner = CliRunner()


ID = "26f737ad-1a5c-4c29-88da-34a702b89320"


OTHER = "3ae77fc2-9bf9-459b-a2e9-7edc5f78335a"


GROUPS = {
    "discovery": [
        "add",
        "import",
        "list",
        "show",
        "run",
        "assess",
        "retry",
        "select",
        "approve",
        "reject",
        "seeds",
    ],
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
        "backfill-tags",
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
    "images": [
        "fetch",
        "retry",
        "show",
        "dispatch",
        "backfill",
        "jobs",
        "topic-logo",
        "source-logo",
        "recompress",
        "resize",
    ],
    "jobs": ["list", "show", "retry", "dispatch"],
    "topics": ["list", "add", "update", "relate", "grant-decision-budget"],
    "tags": ["list", "add", "update"],
    "cache": ["clear"],
    "db": ["upgrade", "check", "current"],
    "search": ["setup", "backfill", "worker"],
}


PATHS = [[name] for name in ("worker", "scheduler", "status")] + [
    [group, command]
    for group, names in GROUPS.items()
    if group != "search"
    for command in names
    if not (group == "discovery" and command == "seeds")
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


@pytest.mark.parametrize("color", [False, True])
def test_invalid_identifier_cannot_emit_terminal_control_commands(operations, monkeypatch, color):
    # Rich disables styling for TERM=dumb even when FORCE_COLOR is set.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    if color:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    payload = "\x1b]52;c;clipboard-change\x07"
    result = runner.invoke(app, ["articles", "show", payload], color=color)

    assert result.exit_code == 2 and not operations
    assert "not a valid" in result.stderr
    if color:
        assert "\x1b[" in result.stderr
    # Rich's own SGR styling is safe; input must not emit any raw control commands.
    message = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
    assert "\x1b" not in message and "\x07" not in message
    assert r"\x1b]52;c;clipboard-change\x07" in message


@pytest.mark.parametrize(
    "path",
    [[], *[[name] for name in GROUPS], *PATHS, *[["search", name] for name in GROUPS["search"]]],
)
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
    if command == "discovery":
        if action == "add":
            args += ["https://example.com/blog"]
        elif action == "import":
            args += ["publishers.opml"]
        elif action in {"show", "assess", "retry", "select", "approve", "reject"}:
            args += [ID]
            if action == "select":
                args += [OTHER]
            if action in {"approve", "reject"}:
                args += ["--by", "tester", "--reason", "Reviewed"]
    elif command == "sources" and action == "add":
        args += ["https://example.com/rss", "--type", "publisher"]
    elif command == "sources" and action == "import":
        args += ["-", "--type", "aggregator"]
    elif command in {"tags"} and action == "add":
        args += ["--name", "Testing", "--slug", "testing"]
    elif command == "topics" and action == "add":
        args += ["--file", "topic.json"]
    elif command == "topics" and action == "relate":
        args += [ID, OTHER, "--relation", "uses_language"]
    elif command == "topics" and action == "grant-decision-budget":
        args += [ID, "--reason", "Capacity restored", "--actor", "test"]
    elif action in {
        "update",
        "show",
        "fetch",
        "enrich",
        "retry",
        "dispatch",
        "enrichment-dispatch",
        "topic-logo",
        "source-logo",
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


def test_actual_entrypoint_preserves_json_stdout_and_exception_messages(monkeypatch):
    monkeypatch.setattr(commands, "source_list", lambda _: [{"id": ID}])
    result = runner.invoke(app, ["sources", "list"])
    assert result.exit_code == 0 and json.loads(result.stdout) == [{"id": ID}]

    def crash(_):
        _local_values = {"diagnostic": "local-value-not-in-message"}
        raise RuntimeError("original-error-message") from None

    monkeypatch.setattr(commands, "source_list", crash)
    result = runner.invoke(app, ["sources", "list"])
    assert result.exit_code == 1 and result.stdout == ""
    assert "original-error-message" in result.stderr
    assert "local-value-not-in-message" not in result.stderr
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


@pytest.fixture
def isolated_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "get_settings", lambda: configuration())
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    monkeypatch.setattr(runtime, "logger", Mock())
    monkeypatch.setattr(runtime, "current", lambda: object())
    engine = Mock(side_effect=AssertionError("Command loaded an unrequested database engine"))
    monkeypatch.setattr(runtime, "get_engine", lru_cache()(engine))
    return engine


@pytest.mark.parametrize("operation", ["add", "list", "sync"])
def test_discovery_seed_leaf_commands_forward_typed_parameters_to_the_selected_operation(
    monkeypatch, isolated_runtime, operation
):
    identifier = uuid.uuid4()
    output = {"created": 1, "existing": 0, "total": 1} if operation == "sync" else []
    handler = Mock(return_value=output)
    monkeypatch.setattr(discovery_commands, "seed_" + operation, handler)
    suffix = {
        "add": ["https://publisher.example/catalog.json", "--format", "json"],
        "list": ["--limit", "12", "--offset", "24"],
        "sync": [str(identifier)],
    }[operation]
    response = CliRunner().invoke(app, ["discovery", "seeds", operation, *suffix])
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout) == output
    (args,) = handler.call_args.args
    if operation == "add":
        assert args.url == "https://publisher.example/catalog.json" and args.format == "json"
    elif operation == "list":
        assert args.limit == 12 and args.offset == 24
    else:
        assert args.id == identifier
    isolated_runtime.assert_not_called()


def test_explicit_budget_grant_keeps_actor_reason_and_capacity_limits_in_the_domain_request(
    monkeypatch, isolated_runtime
):
    proposal_id, job_id, session = uuid.uuid4(), uuid.uuid4(), Mock()
    factory = SimpleNamespace(begin=lambda: nullcontext(session))
    monkeypatch.setattr(db, "session_factory", lambda: factory)
    limits = topic_decision_budget.initial_state(configuration())["limits"]
    # The service returns cumulative capacity; CLI flags request only the increment.
    for name, extra in {
        "calls": 2,
        "tokens": 16000,
        "searches": 4,
        "seconds": 240,
        "escalations": 1,
    }.items():
        limits[name] += extra
    receipt = {"proposal_id": str(proposal_id), "job_id": str(job_id), "limits": limits}
    grant = Mock(return_value=receipt)
    monkeypatch.setattr(topic_decision_budget, "grant_budget", grant)
    response = CliRunner().invoke(
        app,
        [
            "topics",
            "grant-decision-budget",
            str(proposal_id),
            "--reason",
            "Verified upstream evidence is now available",
            "--actor",
            "editor-123",
            "--calls",
            "2",
            "--tokens",
            "16000",
        ],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.stdout) == receipt
    grant.assert_called_once_with(
        session,
        proposal_id,
        calls=2,
        tokens=16000,
        note="Verified upstream evidence is now available",
        actor="editor-123",
    )
    isolated_runtime.assert_not_called()


def test_real_python_module_help_exits_successfully_without_loading_operation_settings(
    monkeypatch, capsys
):
    settings = Mock(side_effect=AssertionError("Offline help loaded operation settings"))
    monkeypatch.setattr(runtime, "get_settings", settings)
    monkeypatch.setattr("sys.argv", ["devfeed", "--help"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("devfeed_cli", run_name="__main__")
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "Usage:" in output and "discovery" in output and "scheduler" in output
    settings.assert_not_called()


def test_article_inspection_retains_private_review_state_without_implicitly_publishing(
    command, isolated_runtime
):
    for link in command.article.topic_links:
        link.origin = "ai"
    response = CliRunner().invoke(app, ["articles", "inspect", str(command.article.id)])
    assert response.exit_code == 0, response.output
    output = json.loads(response.stdout)
    assert output["id"] == str(command.article.id)
    assert output["review_status"] == "approved" and output["publication_status"] == "unpublished"
    assert output["editorial_revision"] == command.article.editorial_revision
    assert output["classification_provenance"] == command.article.classification_provenance
    assert output["publication_blockers"] == []
    assert command.phases == ["open", "closed"]
    command.session.commit.assert_not_called()


@pytest.fixture
def execution(monkeypatch):
    engine, telemetry = Mock(), object()
    engine_provider = lru_cache(maxsize=1)(lambda: engine)
    engine_provider()
    monkeypatch.setattr(runtime, "get_settings", lambda: configuration())
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    logger = Mock()
    monkeypatch.setattr(runtime, "logger", logger)
    monkeypatch.setattr(runtime, "get_engine", engine_provider)
    monkeypatch.setattr(runtime, "current", lambda: None)
    monkeypatch.setattr(runtime, "start_runtime", Mock(return_value=telemetry))
    stop = Mock()
    monkeypatch.setattr(runtime, "stop_runtime", stop)
    return engine, telemetry, stop, logger


def test_status_reports_nonready_dependencies_with_failure_exit_but_preserves_the_report(
    execution, capsys
):
    report = {"dependencies_ready": False, "database": "unavailable", "redis": "ready"}
    run = Mock(return_value=report)
    assert runtime.execute(SimpleNamespace(command="status", execute=run)) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == report and captured.err == ""
    engine, _telemetry, stop, logger = execution
    logger.warning.assert_called_once_with("command_dependencies_unavailable")
    logger.info.assert_called_once_with("command_started")
    engine.dispose.assert_called_once_with()
    stop.assert_not_called()  # Status does not start the cache-writing command runtime.


def test_database_conflict_is_a_safe_operator_rejection_with_runtime_cleanup(execution, capsys):
    run = Mock(side_effect=IntegrityError("private SQL", {}, RuntimeError("private detail")))
    assert runtime.execute(SimpleNamespace(command="sources", action="add", execute=run)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: Conflicting or invalid record; no changes committed.\n"
    engine, telemetry, stop, logger = execution
    logger.warning.assert_called_once_with(
        "command_conflict", extra={"error_type": "IntegrityError"}
    )
    engine.dispose.assert_called_once_with()
    stop.assert_called_once_with(telemetry)


@pytest.mark.parametrize(
    "flag,enabled", [("--auto-link-topic", True), ("--no-auto-link-topic", False)]
)
def test_tag_update_keeps_explicit_automatic_or_manual_link_intent(monkeypatch, flag, enabled):
    current = tag()
    execute = Mock(return_value=0)
    monkeypatch.setattr(runtime, "execute", execute)
    result = CliRunner().invoke(app, ["tags", "update", str(current.id), flag])
    assert result.exit_code == 0, result.output
    request = execute.call_args.args[0]
    assert request.execute is commands.taxonomy_write and request.id == current.id
    assert request.auto_link_topic is enabled
    assert not hasattr(request, "topic_id")  # No implicit clear of the current reviewed link.


def test_tag_list_serializes_the_supplied_scoped_rows_without_writing(monkeypatch):
    current, session = tag(), Mock()
    session.scalars.return_value = [current]
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=session)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(commands, "session_factory", lambda: factory)
    output = commands.taxonomy_list(SimpleNamespace(limit=25, offset=0))
    assert len(output) == 1 and output[0]["id"] == str(current.id)
    assert output[0]["aliases"] == current.aliases
    assert output[0]["auto_link_topic"] is False and output[0]["topic_id"] is None
    session.commit.assert_not_called()
    session.add.assert_not_called()


def validation_failure():
    with pytest.raises(ValidationError) as failure:
        SourceDecision.model_validate({"decision": "invalid", "note": "private-input"})
    return failure.value


@pytest.mark.parametrize(
    "failure,exit_code,message",
    [
        (lambda: CacheUnavailable("private-cache-endpoint"), 1, "Response cache unavailable"),
        (
            lambda: FeedValidationError(FeedError("private-feed-endpoint", status=404)),
            2,
            "HTTP 404 (not found). Check the RSS/Atom URL.",
        ),
        (lambda: CommandError("private-migration-config"), 1, "Migration command failed"),
        (lambda: ValueError("private-connection-string"), 2, "Invalid command input"),
        (validation_failure, 2, "decision: Input should be"),
    ],
)
def test_cli_dependency_and_validation_failures_preserve_recovery_code_without_echoing_secrets(
    monkeypatch, capsys, failure, exit_code, message
):
    engine, telemetry = Mock(), object()
    engine_provider = lru_cache(maxsize=1)(lambda: engine)
    engine_provider()
    monkeypatch.setattr(runtime, "get_settings", lambda: configuration())
    monkeypatch.setattr(runtime, "configure_logging", Mock())
    monkeypatch.setattr(runtime, "logger", Mock())
    monkeypatch.setattr(runtime, "get_engine", engine_provider)
    monkeypatch.setattr(runtime, "current", lambda: None)
    start, stop = Mock(return_value=telemetry), Mock()
    monkeypatch.setattr(runtime, "start_runtime", start)
    monkeypatch.setattr(runtime, "stop_runtime", stop)
    execute = Mock(side_effect=failure())
    assert (
        runtime.execute(SimpleNamespace(command="cache", action="clear", execute=execute))
        == exit_code
    )
    captured = capsys.readouterr()
    assert captured.out == "" and message in captured.err and "private" not in captured.err
    execute.assert_called_once()
    start.assert_called_once_with("cli", serve_metrics=False, profiling=False)
    stop.assert_called_once_with(telemetry)
    engine.dispose.assert_called_once_with()


@pytest.mark.parametrize("fails", [False, True])
def test_cli_drains_analytics_before_telemetry_and_engine_cleanup(execution, monkeypatch, fails):
    events = []
    engine, _, _, _ = execution
    monkeypatch.setattr(runtime, "drain_analytics", lambda: events.append("analytics"))
    monkeypatch.setattr(runtime, "stop_runtime", lambda _: events.append("telemetry"))
    engine.dispose.side_effect = lambda: events.append("engine")

    def execute(_):
        if fails:
            raise RuntimeError("command unavailable")

    assert runtime.execute(SimpleNamespace(command="sources", execute=execute)) == int(fails)
    assert events == ["analytics", "telemetry", "engine"]

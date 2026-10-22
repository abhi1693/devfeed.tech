"""CLI exit codes and explicit taxonomy intent survive real command dispatch."""

import json
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_cli import commands, runtime
from devfeed_cli.main import app
from sqlalchemy.exc import IntegrityError
from test_admin_taxonomy_command_adapters_unit import tag
from test_source_readiness_unit import configuration
from typer.testing import CliRunner


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

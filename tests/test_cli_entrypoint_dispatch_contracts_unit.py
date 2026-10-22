"""Installed CLI paths preserve typed operator intent without opening implicit services."""

import json
import runpy
import uuid
from contextlib import nullcontext
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_cli import discovery_commands, runtime
from devfeed_cli.main import app
from devfeed_core import db, topic_decision_budget
from test_article_readiness_unit import processing_case as processing_case
from test_cli_editorial_command_policy_unit import command as command
from test_source_readiness_unit import configuration
from typer.testing import CliRunner


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

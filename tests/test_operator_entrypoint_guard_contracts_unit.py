"""Operator entrypoints expose configured identity choices and safe recovery errors."""

from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic.util.exc import CommandError
from devfeed_admin_api import auth
from devfeed_admin_api.config import Settings
from devfeed_cli import runtime
from devfeed_core.cache import CacheUnavailable
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.schemas import SourceDecision
from pydantic import ValidationError
from test_admin_auth import oidc_app as oidc_app
from test_source_readiness_unit import configuration


@pytest.mark.parametrize("providers", [[], ["github"], ["google"], ["github", "google"]])
def test_admin_sign_in_choices_advertise_only_validated_configured_providers(
    oidc_app, monkeypatch, providers
):
    settings = Settings(
        _env_file=None,
        **{
            **oidc_app.settings.model_dump(),
            **{f"oidc_{name}_idp_id": name + "-provider" for name in providers},
        },
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    response = oidc_app.client.get("/v1/admin/auth/config")
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "providers": providers}
    assert response.headers["cache-control"] == "no-store"
    assert oidc_app.requests == [] and oidc_app.store.values == {}


@pytest.mark.parametrize("provider", ["github", "google"])
@pytest.mark.parametrize("configured", [False, True])
def test_explicit_provider_login_requires_a_current_configured_choice_before_saving_a_flow(
    oidc_app, monkeypatch, provider, configured
):
    settings = Settings(
        _env_file=None,
        **{
            **oidc_app.settings.model_dump(),
            f"oidc_{provider}_idp_id": provider + "-provider" if configured else None,
        },
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    response = oidc_app.client.get(
        "/v1/admin/auth/login", params={"provider": provider}, follow_redirects=False
    )
    if not configured:
        assert response.status_code == 503
        assert response.json() == {"detail": "Admin sign-in temporarily unavailable"}
        assert oidc_app.store.values == {} and not response.cookies
    else:
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert "urn:zitadel:iam:org:idp:id:" + provider + "-provider" in query["scope"][0].split()
        assert query["code_challenge_method"] == ["S256"]
        assert len(oidc_app.store.values) == 1
        assert response.cookies.get("__Host-devfeed_admin_state")


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

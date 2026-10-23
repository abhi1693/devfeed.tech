"""Reader source previews and suggestions enforce identity, budgets, and safe write failures."""

import uuid
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from devfeed_core.services import ValidatedSource
from devfeed_user_api import sources
from fastapi import HTTPException
from redis.exceptions import ConnectionError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.support.reader_context import user
from tests.users.auth.test_auth import complete, logout_headers
from tests.users.auth.test_auth import oidc_app as oidc_app

PATH = "/v1/user/sources/suggestions"
BODY = {"feed_url": "https://example.com/rss", "source_type": "publisher"}


@pytest.mark.parametrize("operation", ["preview", "suggest"])
@pytest.mark.parametrize("budget", ["limited", "unavailable"])
def test_budget_rejection_precedes_source_validation_and_any_repository_effect(
    monkeypatch, operation, budget
):
    owner, session, redis = user(), Mock(spec=Session), object()
    charge = Mock(return_value=45)
    if budget == "unavailable":
        charge.side_effect = ConnectionError("private-rate-store")
    monkeypatch.setattr(sources, "get_redis", lambda: redis)
    monkeypatch.setattr(sources, "consume_rate_limits", charge)
    validate = Mock()
    monkeypatch.setattr(sources.services, "validate_source", validate)
    body = sources.SourceSuggestion(
        feed_url="https://publisher.example/feed", source_type="publisher"
    )
    with pytest.raises(HTTPException) as error:
        if operation == "preview":
            sources.preview(body, owner)
        else:
            sources.suggest(body, owner, session)
    assert error.value.status_code == (429 if budget == "limited" else 503)
    if budget == "limited":
        assert error.value.headers == {"Retry-After": "45"}
    assert "private-rate-store" not in error.value.detail
    connection, limits = charge.call_args.args
    assert connection is redis and len(limits) == 1
    (limit,) = limits
    namespace = "source-previews" if operation == "preview" else "source-suggestions"
    assert limit.key == f"devfeed:user:{namespace}:{owner.user_id}"
    assert limit.limit == (20 if operation == "preview" else 5) and limit.window == 3600
    validate.assert_not_called()
    assert session.mock_calls == []


def test_successful_preview_only_exposes_validated_feed_name_and_uses_the_lookup_budget(
    monkeypatch,
):
    owner, redis = user(), object()
    charge = Mock(return_value=0)
    monkeypatch.setattr(sources, "get_redis", lambda: redis)
    monkeypatch.setattr(sources, "consume_rate_limits", charge)
    prepared = ValidatedSource(
        name="Engineering publisher",
        feed_url="https://publisher.example/final.xml",
        source_type="publisher",
        enabled=True,
        poll_interval_seconds=3600,
    )
    validate = Mock(return_value=prepared)
    monkeypatch.setattr(sources.services, "validate_source", validate)
    save, profile = Mock(), Mock()
    monkeypatch.setattr(sources.services, "create_source", save)
    monkeypatch.setattr(sources, "request_enrichment", profile)
    body = sources.SourcePreviewRequest(
        feed_url=" https://publisher.example/feed ", source_type="publisher"
    )
    result = sources.preview(body, owner)
    assert result.model_dump() == {"name": "Engineering publisher"}
    (request,) = validate.call_args.args
    assert request.feed_url == "https://publisher.example/feed" and request.enabled is True
    _, limits = charge.call_args.args
    assert limits[0].limit == 20
    save.assert_not_called()
    profile.assert_not_called()


@pytest.mark.parametrize("duplicate", [False, True])
def test_failed_suggestion_write_rechecks_duplicate_identity_before_translating_the_error(
    monkeypatch, duplicate
):
    owner, session = user(), Mock(spec=Session)
    session.begin.return_value = nullcontext()
    session.scalar.side_effect = [None, None, uuid.uuid4() if duplicate else None]
    monkeypatch.setattr(sources, "get_redis", lambda: object())
    monkeypatch.setattr(sources, "consume_rate_limits", Mock(return_value=0))
    prepared = ValidatedSource(
        name="Publisher",
        feed_url="https://publisher.example/feed",
        source_type="publisher",
        enabled=True,
        poll_interval_seconds=3600,
    )
    monkeypatch.setattr(sources.services, "validate_source", Mock(return_value=prepared))
    problem = IntegrityError("private statement", {}, Exception("private database constraint"))
    save = Mock(side_effect=problem)
    monkeypatch.setattr(sources.services, "create_source", save)
    profile = Mock()
    monkeypatch.setattr(sources, "request_enrichment", profile)
    body = sources.SourceSuggestion(feed_url=prepared.feed_url, source_type="publisher")
    with pytest.raises(HTTPException if duplicate else IntegrityError) as error:
        sources.suggest(body, owner, session)
    if duplicate:
        assert error.value.status_code == 409
        assert error.value.detail == "This feed has already been added or suggested."
    else:
        assert error.value is problem
    _, submitted = save.call_args.args
    assert submitted.submitted_by == {
        "name": owner.name,
        "user_id": owner.user_id,
        "verified": True,
    }
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    profile.assert_not_called()


def test_suggestions_require_real_session_and_csrf_before_work(oidc_app, monkeypatch):
    monkeypatch.setattr(sources, "limit_suggestions", lambda *_: pytest.fail("Quota before auth"))
    assert oidc_app.client.post(PATH, json=BODY).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.post(PATH, json=BODY).status_code == 403
    headers = logout_headers(oidc_app)
    headers["Origin"] = "https://attacker.example"
    assert oidc_app.client.post(PATH, json=BODY, headers=headers).status_code == 403


@pytest.mark.parametrize("path", [PATH, PATH + "/preview"])
def test_source_preview_requires_session_and_csrf(oidc_app, path):
    assert oidc_app.client.post(path, json=BODY).status_code == 401
    complete(oidc_app)
    assert oidc_app.client.post(path, json=BODY).status_code == 403

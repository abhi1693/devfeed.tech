"""Reader authentication and daily presentation keep their last boundary checks.

Identity and selection histories are real models. Stored/read rows are supplied
facts; no SQL ownership, atomic claim or provider revocation implementation is emulated.
"""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import UserMustRead
from devfeed_user_api import auth, dependencies, mcp, must_reads, oidc, preferences
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_user_auth import begin, complete
from test_user_auth import oidc_app as oidc_app
from test_user_daily_read_contracts_unit import NOW
from test_user_daily_read_contracts_unit import selection as selection


def test_enabled_signup_measurement_does_not_invent_a_missing_click_cookie(oidc_app):
    oidc_app.settings.x_pixel_enabled = True
    flow = begin(oidc_app)
    stored = json.loads(oidc_app.store.get(auth.key("flow", flow)))
    assert "x_click_id" not in stored
    assert not any("measurement" in request.url.path for request in oidc_app.requests)


def test_identity_expiring_after_protocol_validation_cannot_be_reissued_as_a_reader_session(
    oidc_app, monkeypatch
):
    validate = oidc.protocol.identity

    def provider_identity(*args, **kwargs):
        verified = validate(*args, **kwargs)
        # The real signed token was valid during protocol validation. At the
        # adapter boundary the clock has reached its validated expiry.
        monkeypatch.setattr(oidc, "time", SimpleNamespace(time=lambda: verified["expires_at"]))
        return verified

    monkeypatch.setattr(oidc.protocol, "identity", provider_identity)
    save = Mock()
    monkeypatch.setattr(auth, "save_user_with_status", save)
    response = complete(oidc_app)
    assert response.status_code == 302 and "error=login_failed" in response.headers["location"]
    save.assert_not_called()
    assert not oidc_app.client.cookies.get("__Host-devfeed_user_session")
    assert not any(key.startswith("devfeed:user:session:") for key in oidc_app.store.values)


def test_source_preferences_hide_a_deleted_account_before_loading_saved_follows(selection):
    selection.session.scalar.return_value = None
    with pytest.raises(HTTPException) as unavailable:
        preferences.source_preferences(selection.user, selection.session)
    assert unavailable.value.status_code == 401
    assert unavailable.value.detail == "User account unavailable"
    selection.session.scalars.assert_not_called()
    selection.session.commit.assert_not_called()


def test_daily_presentation_rejects_an_old_local_day_before_loading_any_selection(selection):
    with pytest.raises(HTTPException) as conflict:
        must_reads.present(
            must_reads.Presentation(date=NOW.date() - timedelta(days=1), timezone="UTC"),
            selection.user,
            selection.session,
        )
    assert conflict.value.status_code == 409
    assert selection.session.mock_calls == []


def test_crossing_local_midnight_during_selection_load_does_not_present_the_new_day(
    selection, monkeypatch
):
    before = NOW.replace(hour=23, minute=59, second=59)
    after = before + timedelta(seconds=2)
    selection.state.ranked_at = selection.state.computed_at = before - timedelta(minutes=1)
    selection.state.expires_at = before + timedelta(hours=8)
    reads = iter([before, after])
    monkeypatch.setattr(must_reads, "utcnow", lambda: next(reads))
    selection.session.execute.return_value.all.return_value = []
    with pytest.raises(HTTPException) as conflict:
        must_reads.present(
            must_reads.Presentation(date=before.date(), timezone="UTC"),
            selection.user,
            selection.session,
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "A new daily selection is available"
    selection.session.add.assert_not_called()
    selection.session.commit.assert_not_called()


def test_retained_daily_picks_with_no_remaining_visible_articles_do_not_claim_presentation(
    selection,
):
    saved = UserMustRead(
        user_id=selection.account.id,
        selection_date=NOW.date(),
        timezone="UTC",
        picks=[{"id": str(uuid.uuid4()), "reason": "Because you follow Angular"}],
        presented_at=None,
    )
    existing_read = selection.session.get.side_effect
    selection.session.get.side_effect = lambda model, identifier: (
        saved if model is UserMustRead else existing_read(model, identifier)
    )
    selection.session.scalars.side_effect = [[], []]
    result = must_reads.present(
        must_reads.Presentation(date=NOW.date(), timezone="UTC"),
        selection.user,
        selection.session,
    )
    assert result == {"claimed": False} and saved.presented_at is None
    selection.session.execute.assert_not_called()
    selection.session.add.assert_not_called()
    selection.session.commit.assert_called_once()


def test_invalid_browser_consent_request_id_is_rejected_before_consuming_pending_authority(
    selection, monkeypatch
):
    monkeypatch.setattr(mcp, "browser_user", lambda _: selection.user)
    redis = Mock()
    monkeypatch.setattr(mcp, "get_redis", redis)
    app = FastAPI()
    app.include_router(mcp.router)
    app.dependency_overrides[dependencies.get_session] = lambda: selection.session
    with TestClient(app) as client:
        response = client.post("/v1/user/mcp/requests/invalid", json={"approved": False})
    assert response.status_code == 404
    assert response.json() == {"detail": "Authorization request expired"}
    redis.assert_not_called()

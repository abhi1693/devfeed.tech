"""Private settings reads preserve saved scopes without rewriting account state.

Ownership and stored JSON are already-scoped repository outcomes. These units do
not evaluate account predicates, locks, database transactions or feed selection.
"""

import copy
import uuid
from unittest.mock import Mock

import pytest
from devfeed_core.user_settings import SelectionColumnFeed
from devfeed_user_api import profile
from test_user_profile_write_contracts_unit import api as api


def saved_read(api, value):
    api.session.execute.return_value.one_or_none.return_value = (api.account.id, value)


def test_feed_settings_get_normalizes_saved_preferences_without_replacing_unknown_state(api):
    stored = {
        "view": "stacked",
        "languages": ["fr", "en"],
        "content_types": ["tutorial"],
        "private": "retained",
    }
    before = copy.deepcopy(stored)
    saved_read(api, stored)
    response = api.client.get("/v1/user/settings/feed")
    assert response.status_code == 200
    assert response.json() == {
        "view": "stacked",
        "languages": ["en", "fr"],
        "content_types": ["tutorial"],
        "columns": [],
    }
    assert stored == before
    api.session.commit.assert_not_called()
    api.refresh.assert_not_called()


@pytest.mark.parametrize(
    "feed,expected",
    [("latest", "compact"), ("my_feed", "columns"), ("content:tutorial", "stacked")],
)
def test_feed_view_get_resolves_the_requested_semantic_feed_and_is_never_cacheable(
    api, feed, expected
):
    stored = {"view": "stacked", "feed_views": {"latest": "compact", "my_feed": "columns"}}
    saved_read(api, stored)
    response = api.client.get("/v1/user/settings/feed-view", params={"feed": feed})
    assert response.status_code == 200 and response.json() == {"view": expected}
    assert response.headers["cache-control"] == "no-store"
    assert stored == {"view": "stacked", "feed_views": {"latest": "compact", "my_feed": "columns"}}
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("workspace", ["latest", "my_feed"])
def test_columns_get_keeps_independent_saved_workspaces_and_explicit_empty_initialization(
    api, workspace
):
    latest = {"id": str(uuid.uuid4()), "feed": {"kind": "latest"}}
    stored = {
        "columns": [latest],
        "columns_initialized": True,
        "my_feed_columns": [],
        "my_feed_columns_initialized": True,
    }
    saved_read(api, stored)
    response = api.client.get("/v1/user/settings/columns", params={"workspace": workspace})
    assert response.status_code == 200
    assert response.json() == {
        "initialized": True,
        "columns": [latest] if workspace == "latest" else [],
    }
    api.session.commit.assert_not_called()
    api.refresh.assert_not_called()


def test_column_starters_get_passes_current_preferences_and_account_to_the_small_read_service(
    api, monkeypatch
):
    saved_read(api, {"languages": ["fr"], "content_types": ["tutorial"]})
    selected = SelectionColumnFeed(kind="topic", id=uuid.uuid4())
    suggestions = Mock(return_value=[selected])
    monkeypatch.setattr(profile, "personal_column_starters", suggestions)
    response = api.client.get("/v1/user/settings/columns/starters")
    assert response.status_code == 200 and response.json() == {
        "feeds": [selected.model_dump(mode="json")]
    }
    assert response.headers["cache-control"] == "no-store"
    session, owner, settings = suggestions.call_args.args
    assert session is api.session and owner == api.account.id
    assert settings.languages == ["fr"] and settings.content_types == ["tutorial"]
    api.session.commit.assert_not_called()
    api.refresh.assert_not_called()


@pytest.mark.parametrize("path", ["feed", "feed-view?feed=latest", "columns", "columns/starters"])
def test_account_disappearing_before_private_settings_read_is_unauthorized_without_writes(
    api, monkeypatch, path
):
    api.session.execute.return_value.one_or_none.return_value = None
    suggestions = Mock()
    monkeypatch.setattr(profile, "personal_column_starters", suggestions)
    response = api.client.get(f"/v1/user/settings/{path}")
    assert response.status_code == 401 and response.json() == {"detail": "User account unavailable"}
    suggestions.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("section", ["appearance", "notifications"])
def test_small_saved_settings_reads_return_validated_account_documents(api, section):
    stored = (
        {"theme": "dark", "timezone": "Asia/Kolkata", "date_format": "iso"}
        if section == "appearance"
        else {"show_badge": False, "sound": True}
    )
    api.session.scalar.return_value = stored
    response = api.client.get(f"/v1/user/settings/{section}")
    assert response.status_code == 200
    for field, value in stored.items():
        assert response.json()[field] == value
    assert "private@example.test" not in response.text
    api.session.commit.assert_not_called()

"""Preference validation and reconciled polling retain unrelated saved choices.

Selected preference/source rows are repository boundaries. Units exercise JSON
merges, validation and schedule calculations, not row locking or SQL filtering.
"""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core import config, polling, polling_reconciliation, user_settings
from devfeed_core.models import AdminPreference
from pydantic import ValidationError

from tests.support.source_readiness import NOW, configuration, source


@pytest.fixture
def preference(monkeypatch):
    owner = user_settings.owner_key(
        issuer="https://identity.example", subject="operator", organization_id="team"
    )
    original = user_settings.UserSettings(
        profile={"display_name": "Saved operator"},
        appearance={"theme": "dark"},
        tables={"articles": {"columns": {"source": False}, "query": {"q": "Python"}}},
    )
    stored = AdminPreference(
        owner_key=owner,
        settings=original.model_dump(mode="json"),
        updated_at=NOW - timedelta(days=1),
    )
    session = Mock()
    session.scalar.return_value = stored
    session.get.return_value = stored
    monkeypatch.setattr(user_settings, "utcnow", lambda: NOW)
    return SimpleNamespace(owner=owner, row=stored, session=session)


@pytest.mark.parametrize("exists", [False, True])
def test_saved_preferences_or_defaults_are_read_without_creating_an_account_record(
    preference, exists
):
    v = preference
    v.session.get.return_value = v.row if exists else None
    result = user_settings.read_settings(v.session, v.owner)
    assert result.profile.display_name == ("Saved operator" if exists else None)
    assert result.appearance.theme == ("dark" if exists else "system")
    v.session.get.assert_called_once_with(AdminPreference, v.owner)
    v.session.add.assert_not_called()
    v.session.execute.assert_not_called()


@pytest.mark.parametrize(
    "section,value",
    [
        ("profile", {"display_name": "  Updated operator  "}),
        ("notifications", {"show_badge": False, "sound": True}),
        ("defaults", {"page_size": 50}),
    ],
)
def test_preference_write_validates_one_section_and_preserves_other_saved_choices(
    preference, section, value
):
    v = preference
    before = copy.deepcopy(v.row.settings)
    result = user_settings.write_settings(v.session, v.owner, section, value)
    assert result.model_dump(mode="json")[section] == v.row.settings[section]
    if section == "profile":
        assert result.profile.display_name == "Updated operator"
    elif section == "notifications":
        assert not result.notifications.show_badge and result.notifications.sound
    else:
        assert result.defaults.page_size == 50
    assert v.row.settings["appearance"] == before["appearance"]
    assert v.row.settings["tables"] == before["tables"]
    assert v.row.updated_at == NOW
    v.session.flush.assert_called_once()
    v.session.commit.assert_not_called()


def test_table_patch_keeps_saved_columns_when_only_query_is_changed(preference):
    v = preference
    result = user_settings.write_settings(
        v.session, v.owner, "tables", {"query": {"q": "Rust", "sort": "-created"}}, table="articles"
    )
    assert result.tables["articles"].columns == {"source": False}
    assert result.tables["articles"].query == {"q": "Rust", "sort": "-created"}
    assert result.profile.display_name == "Saved operator" and result.appearance.theme == "dark"
    v.session.flush.assert_called_once()


@pytest.mark.parametrize(
    "case", ["invalid-key", "too-many", "invalid-profile", "invalid-table-value"]
)
def test_invalid_preference_payload_cannot_replace_the_previous_saved_document(preference, case):
    v = preference
    if case == "too-many":
        v.row.settings["tables"] = {
            f"table_{index}": {"columns": {}, "query": {}} for index in range(64)
        }
    before, old_time = copy.deepcopy(v.row.settings), v.row.updated_at
    options = {
        "invalid-key": ("tables", {"query": {}}, "bad/table"),
        "too-many": ("tables", {"query": {}}, "new_table"),
        "invalid-profile": ("profile", {"role": "superuser"}, None),
        "invalid-table-value": ("tables", {"columns": {"source": "not-a-boolean"}}, "articles"),
    }[case]
    with pytest.raises((ValueError, ValidationError)):
        user_settings.write_settings(v.session, v.owner, options[0], options[1], table=options[2])
    assert v.row.settings == before and v.row.updated_at == old_time
    v.session.flush.assert_not_called()


def test_existing_table_can_be_updated_at_the_saved_table_capacity(preference):
    v = preference
    v.row.settings["tables"] = {
        f"table_{index}": {"columns": {}, "query": {}} for index in range(64)
    }
    result = user_settings.write_settings(
        v.session, v.owner, "tables", {"columns": {"name": False}}, table="table_0"
    )
    assert len(result.tables) == 64 and result.tables["table_0"].columns == {"name": False}


def test_legacy_profile_links_are_normalized_but_duplicate_card_selections_are_refused():
    profile = user_settings.UserProfileUpdate(links=["https://developer.example/"], location="")
    assert profile.links[0].url == "https://developer.example/" and profile.location is None
    assert user_settings.DevCardSettings(technologies=None).technologies is None
    with pytest.raises(ValidationError, match="unique"):
        user_settings.DevCardSettings(technologies=[uuid.UUID(int=1), uuid.UUID(int=1)])
    with pytest.raises(ValidationError):
        user_settings.UserProfileUpdate(links={"url": "https://developer.example/"})


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("last_success", [None, "recent", "overdue"])
def test_polling_reconciliation_uses_real_cadence_and_spreads_overdue_sources_deterministically(
    monkeypatch,
    enabled,
    last_success,
):
    settings = configuration(adaptive_source_polling_enabled=enabled)
    monkeypatch.setattr(polling_reconciliation, "get_settings", lambda: settings)
    monkeypatch.setattr(polling, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    # This selected row's applied mode disagrees with the current feature gate.
    current = source(
        id=uuid.UUID(int=17),
        approval="approved",
        polling_mode="adaptive",
        scheduled_polling_mode="fixed" if enabled else "adaptive",
        scheduled_interval_seconds=1800,
        poll_interval_seconds=1800,
        polling_state={},
        consecutive_failures=0,
        last_success_at=None
        if last_success is None
        else NOW - timedelta(seconds=10)
        if last_success == "recent"
        else NOW - timedelta(days=1),
    )
    expected_source = copy.deepcopy(current)
    due = polling.schedule(expected_source, current.last_success_at or NOW)
    session, factory = MagicMock(), MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    session.scalars.return_value.all.return_value = [current]
    assert polling_reconciliation.reconcile_polling(factory, 1, NOW) == 1
    assert current.next_fetch_at == max(due, NOW + timedelta(seconds=17))
    assert current.scheduled_polling_mode == ("adaptive" if enabled else "fixed")
    assert current.scheduled_interval_seconds == expected_source.scheduled_interval_seconds
    assert current.approval_status == "approved" and current.enabled
    session.add.assert_not_called()

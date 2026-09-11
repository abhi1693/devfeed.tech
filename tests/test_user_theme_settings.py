"""Saved display preferences are isolated by the authenticated user identity."""

import pytest
from devfeed_core.models import UserAccount
from devfeed_user_api.accounts import save_user
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration
PATH = "/v1/user/settings/appearance"
DEFAULTS = {
    "theme": "system",
    "timezone": "local",
    "date_format": "locale",
    "time_format": "system",
}


def test_preferences_persist_and_do_not_change_other_account_settings(user_data, database):
    client, current, first, second, _ = user_data
    assert client.get(PATH).json() == DEFAULTS
    client.put("/v1/user/settings/profile", json={"display_name": "Python Fan"})
    saved = {"theme": "dark", "timezone": "Asia/Kolkata", "date_format": "iso", "time_format": "24"}
    response = client.put(PATH, json=saved)
    assert response.status_code == 200 and response.json() == saved
    save_user(
        {
            "issuer": current.issuer,
            "subject": current.subject,
            "organization_id": current.organization_id,
            "name": "New provider name",
            "email": "new@example.test",
        }
    )
    assert client.get(PATH).json() == saved
    with database() as session:
        assert session.get(UserAccount, first).profile["display_name"] == "Python Fan"
        assert session.get(UserAccount, second).appearance_settings == {}
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == DEFAULTS


@pytest.mark.parametrize("field", ["subject", "issuer", "organization_id"])
def test_appearance_settings_require_matching_identity(user_data, field):
    client, current, *_ = user_data
    setattr(current, field, "wrong-owner")
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json=DEFAULTS).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "victim"},
        {"theme": "invalid"},
        {"theme": None},
        {"timezone": "Not/A_Zone"},
        {"time_format": "13"},
        {"date_format": "invalid"},
        {"email": "other@example.test"},
    ],
)
def test_invalid_preferences_are_rejected_without_overwriting(user_data, payload):
    client = user_data[0]
    assert client.put(PATH, json=payload).status_code == 422
    assert client.get(PATH).json() == DEFAULTS


def test_appearance_settings_reset(user_data):
    client = user_data[0]
    client.put(PATH, json={"theme": "dark"})
    assert client.put(PATH, json={}).json() == DEFAULTS

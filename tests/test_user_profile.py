"""Profile edits belong to the signed-in account and survive provider refreshes."""

import pytest
from devfeed_core.models import UserAccount
from devfeed_user_api.accounts import save_user
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


def test_profile_is_saved_without_changing_sign_in_identity(user_data, database):
    client, current, first, second, _ = user_data
    assert client.get("/v1/user/settings/profile").json() == {
        "display_name": None,
        "avatar_url": None,
    }
    value = {"display_name": "  Python Fan  ", "avatar_url": "https://example.com/avatar.png"}
    saved = client.put("/v1/user/settings/profile", json=value)
    assert saved.status_code == 200
    assert saved.json()["display_name"] == "Python Fan"
    with database() as session:
        account = session.get(UserAccount, first)
        assert account.name == "User" and account.email == "same@example.test"
        assert session.get(UserAccount, second).profile == {}
    save_user(
        {
            "issuer": current.issuer,
            "subject": current.subject,
            "organization_id": current.organization_id,
            "name": "New provider name",
            "email": "new@example.test",
        }
    )
    assert client.get("/v1/user/settings/profile").json() == saved.json()
    current.user_id, current.subject = str(second), "user-b"
    assert client.get("/v1/user/settings/profile").json() == {
        "display_name": None,
        "avatar_url": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": "x" * 101},
        {"email": "other@example.com"},
        {"user_id": "victim"},
        {"avatar_url": "javascript:alert(1)"},
        {"avatar_url": "http://127.0.0.1/private"},
        {"avatar_url": "https://user:pass@example.com/avatar.png"},
    ],
)
def test_profile_rejects_invalid_or_identity_fields(user_data, payload):
    client = user_data[0]
    assert client.put("/v1/user/settings/profile", json=payload).status_code == 422
    assert client.get("/v1/user/settings/profile").json()["display_name"] is None


def test_profile_checks_identity_binding_for_read_and_write(user_data):
    client, current, *_ = user_data
    current.subject = "wrong-owner"
    assert client.get("/v1/user/settings/profile").status_code == 401
    assert (
        client.put("/v1/user/settings/profile", json={"display_name": "Wrong"}).status_code == 401
    )


def test_reset_to_defaults_normalizes_empty_values(user_data):
    client = user_data[0]
    client.put("/v1/user/settings/profile", json={"display_name": "Override"})
    assert client.put(
        "/v1/user/settings/profile", json={"display_name": " ", "avatar_url": ""}
    ).json() == {"display_name": None, "avatar_url": None}

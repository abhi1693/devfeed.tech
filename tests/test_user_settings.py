from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from devfeed_admin_api.auth import AdminIdentity, require_admin
from devfeed_core.user_settings import (
    AppearanceSettings,
    DefaultSettings,
    ProfileSettings,
    owner_key,
    read_settings,
    write_settings,
)
from pydantic import ValidationError


def test_settings_reject_unsafe_profiles_and_invalid_display_values():
    for value in ("javascript:alert(1)", "http://127.0.0.1/private", "https://u:p@example.com/a"):
        with pytest.raises(ValidationError):
            ProfileSettings(avatar_url=value)
    with pytest.raises(ValidationError):
        ProfileSettings.model_validate({"roles": ["superuser"]})
    with pytest.raises(ValidationError):
        AppearanceSettings(timezone="Not/A_Timezone")
    with pytest.raises(ValidationError):
        DefaultSettings.model_validate({"landing_page": "https://example.com/"})
    with pytest.raises(ValidationError):
        DefaultSettings.model_validate({"refresh_seconds": 1})
    assert ProfileSettings(display_name="  ", avatar_url="").display_name is None
    assert AppearanceSettings(timezone="Asia/Kolkata").timezone == "Asia/Kolkata"


@pytest.mark.integration
def test_settings_persist_per_identity_and_sections_do_not_overwrite_each_other(admin_client):
    original = admin_client.get("/v1/admin/settings").json()
    assert original["defaults"]["refresh_seconds"] == 10
    assert original["notifications"] == {"show_badge": True, "sound": False}
    assert (
        admin_client.put(
            "/v1/admin/settings/profile", json={"display_name": "Preferred name"}
        ).status_code
        == 200
    )
    assert (
        admin_client.put(
            "/v1/admin/settings/appearance", json={"theme": "dark", "timezone": "Asia/Kolkata"}
        ).status_code
        == 200
    )
    saved = admin_client.get("/v1/admin/settings").json()
    assert saved["profile"]["display_name"] == "Preferred name"
    assert saved["appearance"]["theme"] == "dark"
    assert (
        admin_client.put(
            "/v1/admin/settings/profile", json={"subject": "other", "roles": ["superuser"]}
        ).status_code
        == 422
    )

    def identity_override(identity):
        def current():
            return identity

        return current

    for changes in (
        {"subject": "other"},
        {"issuer": "https://other.example"},
        {"organization_id": "other"},
    ):
        values = dict(
            subject="integration-admin",
            issuer="https://identity.example",
            organization_id="integration-org",
            roles=["superuser"],
            expires_at=4102444800,
            csrf_token="test-csrf",
        )
        admin_client.app.dependency_overrides[require_admin] = identity_override(
            AdminIdentity(**{**values, **changes})
        )
        assert admin_client.get("/v1/admin/settings").json() == original


@pytest.mark.integration
def test_table_updates_preserve_columns_and_filters_and_reset_only_table_preferences(admin_client):
    admin_client.put("/v1/admin/settings/profile", json={"display_name": "Editor"})
    url = "/v1/admin/settings/tables/topic-proposals"
    assert admin_client.patch(url, json={"columns": {"keywords": False}}).status_code == 200
    assert (
        admin_client.patch(url, json={"query": {"status": "pending", "q": "test"}}).status_code
        == 200
    )
    table = admin_client.get("/v1/admin/settings").json()["tables"]["topic-proposals"]
    assert table == {"columns": {"keywords": False}, "query": {"status": "pending", "q": "test"}}
    assert admin_client.patch(url, json={"query": {"q": "x" * 501}}).status_code == 422
    reset = admin_client.delete("/v1/admin/settings/tables").json()
    assert reset["tables"] == {}
    assert reset["profile"]["display_name"] == "Editor"


@pytest.mark.integration
def test_concurrent_section_writes_are_serialized_without_lost_updates(database):
    owner = owner_key(issuer="test", organization_id="test", subject="test")
    barrier = Barrier(2)

    def save(section, value):
        barrier.wait(timeout=5)
        with database.begin() as session:
            write_settings(session, owner, section, value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        profile = pool.submit(save, "profile", {"display_name": "Editor"})
        appearance = pool.submit(save, "appearance", {"theme": "dark"})
        profile.result(timeout=10)
        appearance.result(timeout=10)
    with database() as session:
        settings = read_settings(session, owner)
        assert settings.profile.display_name == "Editor"
        assert settings.appearance.theme == "dark"

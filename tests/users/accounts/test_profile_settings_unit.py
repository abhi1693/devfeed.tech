"""Profile mutations and saved settings reads preserve account ownership and unrelated choices."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from devfeed_core.models import UserAccount, UserLink, UserStackAssociation
from devfeed_core.user_settings import SelectionColumnFeed, UserProfileSettings
from devfeed_user_api import avatars, dependencies, profile
from devfeed_user_api.auth import UserIdentity, require_user
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Update

from tests.users.accounts.test_avatar_uploads import picture


@pytest.fixture
def api(monkeypatch):
    account = UserAccount(
        id=uuid.uuid4(),
        issuer="https://identity.example",
        subject="reader",
        organization_id="readers",
        name="Provider name",
        email="private@example.test",
        username=None,
        about="Existing about",
        profile={
            "display_name": "Saved name",
            "bio": "Saved biography",
            "location": "Bengaluru",
            "experience_level": "senior",
            "visibility": {"public": False, "achievements": True},
            "dev_card": {"motion": "static", "theme": "terminal", "accent": "teal"},
        },
    )
    user = UserIdentity(
        user_id=str(account.id),
        issuer=account.issuer,
        subject=account.subject,
        organization_id=account.organization_id,
        expires_at=4_102_444_800,
        csrf_token="test-csrf",
    )
    session = Mock(spec=Session)
    session.scalar.return_value = account.id
    session.get.return_value = account
    projection = Mock(
        side_effect=lambda _, row: UserProfileSettings.model_validate(
            {**row.profile, "username": row.username, "about": row.about}
        )
    )
    monkeypatch.setattr(profile, "profile_value", projection)
    monkeypatch.setattr(avatars, "profile_value", projection)
    invalidate = Mock()
    monkeypatch.setattr(profile, "invalidate_public_cache", invalidate)
    monkeypatch.setattr(avatars, "invalidate_public_cache", invalidate)
    storage = MagicMock()
    storage.__enter__.return_value = storage
    variants = {size: picture(size=(size, size), format="WEBP") for size in (32, 64, 128, 256, 512)}
    storage.normalize.return_value = (variants, {size: size for size in variants})
    storage.replace.return_value = [
        {"width": size, "url": f"https://images.example/avatar/{size}.webp?v=2"}
        for size in (32, 64, 128, 256, 512)
    ]
    storage_factory = Mock(return_value=storage)
    monkeypatch.setattr(profile, "AvatarStorage", storage_factory)
    monkeypatch.setattr(avatars, "AvatarStorage", storage_factory)
    refresh = Mock()
    monkeypatch.setattr(profile, "request_recommendation_refresh", refresh)
    app = FastAPI()
    app.include_router(profile.router)
    app.include_router(avatars.router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(
            account=account,
            user=user,
            session=session,
            client=client,
            projection=projection,
            invalidate=invalidate,
            storage=storage,
            storage_factory=storage_factory,
            refresh=refresh,
        )


def feed_read(api, stored):
    api.session.execute.side_effect = [
        SimpleNamespace(one_or_none=lambda: (api.account.id, stored)),
        Mock(),
    ]


def saved_settings(api):
    command = api.session.execute.call_args.args[0]
    assert isinstance(command, Update)
    parameters = command.compile().params
    assert api.account.id in parameters.values()
    return parameters["feed_settings"]


def column(kind="latest"):
    return {"id": str(uuid.uuid4()), "feed": {"kind": kind}}


def test_partial_profile_edit_merges_card_and_visibility_without_replacing_private_identity(api):
    before = copy.deepcopy(api.account.profile)

    response = api.client.put(
        "/v1/user/settings/profile",
        json={
            "display_name": " Updated name ",
            "about": None,
            "experience_level": None,
            "dev_card": {"accent": "rose"},
            "visibility": {"public": True},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Updated name"
    assert api.account.about is None and api.account.profile["experience_level"] is None
    assert api.account.profile["bio"] == before["bio"]
    assert api.account.profile["location"] == before["location"]
    assert api.account.profile["dev_card"] == {
        "motion": "static",
        "theme": "terminal",
        "accent": "rose",
    }
    assert api.account.profile["visibility"]["public"] is True
    assert api.account.profile["visibility"]["achievements"] is True
    assert (api.account.name, api.account.email) == ("Provider name", "private@example.test")
    assert "private@example.test" not in response.text
    api.session.commit.assert_called_once()
    api.invalidate.assert_called_once_with(reasons=("profile",))


def test_username_claim_is_normalized_and_cannot_be_replaced_by_a_later_profile_patch(api):
    first = api.client.put("/v1/user/settings/profile", json={"username": " New-Reader "})
    assert first.status_code == 200 and api.account.username == "new-reader"
    api.session.commit.reset_mock()
    api.invalidate.reset_mock()

    response = api.client.put("/v1/user/settings/profile", json={"username": "other-reader"})

    assert response.status_code == 409 and api.account.username == "new-reader"
    api.session.commit.assert_not_called()
    api.invalidate.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
def test_stack_can_retain_retired_topics_but_cannot_newly_select_them(api, retained):
    identifier = uuid.uuid4()
    api.session.execute.return_value = SimpleNamespace(all=lambda: [(identifier, "rejected")])
    api.session.scalars.return_value = [identifier] if retained else []
    if retained:
        api.projection.side_effect = None
        api.projection.return_value = UserProfileSettings.model_validate(
            {
                **api.account.profile,
                "stack": [
                    {
                        "topic_id": identifier,
                        "section": "past",
                        "since_year": 2020,
                        "name": "Python",
                        "slug": "python",
                        "kind": "language",
                        "logo_url": None,
                        "status": "rejected",
                    }
                ],
            }
        )

    response = api.client.put(
        "/v1/user/settings/profile",
        json={"stack": [{"topic_id": str(identifier), "section": "past", "since_year": 2020}]},
    )

    assert response.status_code == (200 if retained else 422), response.text
    if retained:
        assert response.json()["stack"][0]["status"] == "rejected"
        rows = list(api.session.add_all.call_args.args[0])
        assert len(rows) == 1 and isinstance(rows[0], UserStackAssociation)
        assert (rows[0].user_id, rows[0].topic_id, rows[0].position, rows[0].section) == (
            api.account.id,
            identifier,
            0,
            "past",
        )
        api.session.commit.assert_called_once()
    else:
        api.session.add_all.assert_not_called()
        api.session.commit.assert_not_called()
        api.invalidate.assert_not_called()


def test_links_are_normalized_and_written_in_explicit_user_order(api):
    links = [
        {"url": "https://example.com/", "label": None},
        {"url": "https://github.com/reader", "label": "Code"},
    ]
    api.projection.side_effect = None
    api.projection.return_value = UserProfileSettings.model_validate(
        {**api.account.profile, "links": links}
    )
    response = api.client.put(
        "/v1/user/settings/profile",
        json={
            "links": [
                "https://EXAMPLE.com",
                {"url": "https://github.com/reader", "label": " Code "},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["links"] == links
    rows = list(api.session.add_all.call_args.args[0])
    assert all(isinstance(row, UserLink) and row.user_id == api.account.id for row in rows)
    assert [(row.position, row.url, row.label) for row in rows] == [
        (0, "https://example.com/", None),
        (1, "https://github.com/reader", "Code"),
    ]


@pytest.mark.parametrize("replace", [False, True])
def test_profile_avatar_replacement_removes_managed_slots_only_when_the_url_changes(api, replace):
    original = "https://images.example/avatar/256.webp?v=1"
    api.account.profile.update(
        avatar_url=original, avatar_variants=[{"width": 256, "url": original}]
    )

    response = api.client.put(
        "/v1/user/settings/profile",
        json={"avatar_url": "https://publisher.example/photo.png" if replace else original},
    )

    assert response.status_code == 200
    if replace:
        assert api.account.profile["avatar_variants"] == []
        api.storage.remove.assert_called_once()
        api.storage_factory.assert_called_once_with(api.account.id)
    else:
        assert api.account.profile["avatar_variants"]
        api.storage_factory.assert_not_called()


@pytest.mark.parametrize("languages,refresh", [(["en"], False), (["ja", "ja"], True)])
def test_legacy_feed_patch_preserves_columns_and_refreshes_only_for_language_change(
    api, languages, refresh
):
    saved_column = column()
    stored = {"languages": ["en"], "columns": [saved_column], "future_option": {"keep": True}}
    feed_read(api, stored)

    response = api.client.put(
        "/v1/user/settings/feed", json={"languages": languages, "view": "compact"}
    )

    assert response.status_code == 200, response.text
    saved = saved_settings(api)
    assert saved["columns"] == [saved_column] and saved["future_option"] == {"keep": True}
    assert saved["languages"] == sorted(set(languages))
    if refresh:
        api.refresh.assert_called_once_with(api.session, api.account.id)
    else:
        api.refresh.assert_not_called()
    api.session.commit.assert_called_once()


def test_explicit_latest_columns_change_preserves_the_inherited_my_feed_workspace(api):
    previous, replacement = column(), column("recommended")
    feed_read(api, {"columns": [previous], "columns_initialized": True, "languages": ["en"]})

    response = api.client.put("/v1/user/settings/feed", json={"columns": [replacement]})

    assert response.status_code == 200
    saved = saved_settings(api)
    assert saved["columns"] == [replacement] and saved["columns_initialized"] is True
    assert saved["my_feed_columns"] == [previous] and saved["my_feed_columns_initialized"] is True


@pytest.mark.parametrize("workspace", ["latest", "my_feed"])
@pytest.mark.parametrize("mode", ["initialize", "restore_empty", "replace"])
def test_column_installation_cannot_overwrite_an_existing_workspace_without_explicit_replacement(
    api, workspace, mode
):
    old, new = column(), column("recommended")
    prefix = "my_feed_" if workspace == "my_feed" else ""
    feed_read(api, {f"{prefix}columns": [old], f"{prefix}columns_initialized": True})

    response = api.client.put(
        "/v1/user/settings/columns",
        params={"workspace": workspace},
        json={"columns": [new], "mode": mode},
    )

    assert response.status_code == 200
    assert response.json()["columns"] == ([new] if mode == "replace" else [old])
    if mode == "replace":
        assert saved_settings(api)[f"{prefix}columns"] == [new]
        api.session.commit.assert_called_once()
    else:
        assert api.session.execute.call_count == 1
        api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "scope,view,status",
    [("latest", "columns", 422), ("my_feed", "columns", 200), ("latest", "compact", 200)],
)
def test_feed_view_write_targets_only_its_scope_and_freezes_the_legacy_default(
    api, scope, view, status
):
    feed_read(
        api, {"view": "stacked", "feed_views": {"read_later": "compact"}, "languages": ["ja"]}
    )

    response = api.client.put(
        "/v1/user/settings/feed-view", params={"feed": scope}, json={"view": view}
    )

    assert response.status_code == status
    if status == 200:
        assert response.headers["Cache-Control"] == "no-store"
        saved = saved_settings(api)
        assert saved["feed_views"] == {"read_later": "compact", scope: view}
        assert saved["feed_view_default"] == "stacked" and saved["languages"] == ["ja"]
        api.session.commit.assert_called_once()
    else:
        assert api.session.execute.call_count == 1
        api.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "section,payload",
    [
        ("notifications", {"show_badge": False, "sound": True}),
        (
            "appearance",
            {
                "theme": "dark",
                "timezone": "Asia/Kolkata",
                "time_format": "24",
                "date_format": "iso",
            },
        ),
    ],
)
def test_small_settings_commands_return_validated_values_and_target_the_owned_account(
    api, section, payload
):
    response = api.client.put(f"/v1/user/settings/{section}", json=payload)

    assert response.status_code == 200 and response.json() == payload
    command = api.session.execute.call_args.args[0]
    assert isinstance(command, Update)
    parameters = command.compile().params
    assert api.account.id in parameters.values()
    field = "notification_settings" if section == "notifications" else "appearance_settings"
    assert parameters[field] == payload
    api.session.commit.assert_called_once()


@pytest.mark.parametrize("section", ["notifications", "appearance"])
def test_missing_private_settings_are_an_account_error_not_default_preferences(api, section):
    api.session.scalar.return_value = None

    response = api.client.get(f"/v1/user/settings/{section}")

    assert response.status_code == 401
    api.session.commit.assert_not_called()


def test_avatar_upload_selects_the_256_variant_and_preserves_other_profile_fields(api):
    body = picture()
    response = api.client.post(
        "/v1/user/settings/profile/avatar",
        files={"file": ("profile.png", body, "image/png")},
    )

    assert response.status_code == 200, response.text
    api.storage.normalize.assert_called_once_with(body)
    variants, widths = api.storage.normalize.return_value
    api.storage.replace.assert_called_once_with(variants, widths=widths)
    assert response.json()["avatar_url"] == "https://images.example/avatar/256.webp?v=2"
    assert response.json()["bio"] == "Saved biography"
    assert len(response.json()["avatar_variants"]) == 5
    api.session.commit.assert_called_once()
    api.invalidate.assert_called_once_with(reasons=("avatar",))


def test_unsupported_avatar_media_type_never_reaches_normalization_or_storage(api):
    response = api.client.post(
        "/v1/user/settings/profile/avatar",
        files={"file": ("profile.svg", b"<svg/>", "image/svg+xml")},
    )

    assert response.status_code == 415
    api.storage_factory.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("managed", [False, True])
def test_avatar_removal_is_valid_without_existing_slots_and_preserves_the_biography(api, managed):
    api.account.profile.update(
        avatar_url="https://images.example/256.webp",
        avatar_variants=[{"width": 256, "url": "https://images.example/256.webp"}]
        if managed
        else [],
    )

    response = api.client.delete("/v1/user/settings/profile/avatar")

    assert response.status_code == 200 and response.json()["avatar_variants"] == []
    assert response.json()["avatar_url"] is None and response.json()["bio"] == "Saved biography"
    if managed:
        api.storage.remove.assert_called_once()
    else:
        api.storage_factory.assert_not_called()
    api.session.commit.assert_called_once()
    api.invalidate.assert_called_once_with(reasons=("avatar",))


def test_avatar_storage_failure_does_not_request_a_commit_or_public_cache_invalidation(api):
    api.storage.replace.side_effect = HTTPException(503, "Image storage unavailable")

    response = api.client.post(
        "/v1/user/settings/profile/avatar",
        files={"file": ("profile.png", picture(), "image/png")},
    )

    assert response.status_code == 503
    api.session.commit.assert_not_called()
    api.invalidate.assert_not_called()


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

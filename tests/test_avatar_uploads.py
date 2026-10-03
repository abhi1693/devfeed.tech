"""Owned uploads overwrite fixed S3 objects and preserve profile edits on failures."""

import io
import time
from urllib.parse import urlsplit

import pytest
from botocore.exceptions import ClientError
from devfeed_core.config import get_settings
from devfeed_core.models import UserAccount
from devfeed_user_api import avatar_storage
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image


def picture(color="red", size=(120, 80), format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=format)
    return output.getvalue()


class FakeR2:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.fail_next = None

    def get_object(self, **kwargs):
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[kwargs["Key"]])}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        if self.fail_next == kwargs["Key"]:
            self.fail_next = None  # Simulate timeout after the write reached R2.
            raise OSError("storage timeout")

    def delete_object(self, **kwargs):
        self.objects.pop(kwargs["Key"], None)

    def close(self):
        pass


@pytest.fixture
def uploaded_reader(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example.test")
    get_settings.cache_clear()
    r2 = FakeR2()
    monkeypatch.setattr(avatar_storage, "storage_client", lambda: r2)
    with database.begin() as session:
        account = UserAccount(
            issuer="https://identity.example.test",
            subject="reader",
            organization_id="readers",
            username="reader",
            profile={
                "display_name": "Reader",
                "bio": "Preserve this",
                "visibility": {"public": True},
            },
        )
        session.add(account)
        session.flush()
        identifier = account.id
    user = UserIdentity(
        user_id=str(identifier),
        issuer="https://identity.example.test",
        subject="reader",
        organization_id="readers",
        expires_at=int(time.time()) + 3600,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, r2, user, identifier


@pytest.mark.integration
def test_upload_replaces_every_size_without_history_and_projects_public_variants(uploaded_reader):
    client, r2, _, identifier = uploaded_reader
    response = client.post(
        "/v1/user/settings/profile/avatar",
        files={"file": ("../../someone/avatar.png", picture(), "image/png")},
    )
    assert response.status_code == 200
    first = response.json()
    assert first["bio"] == "Preserve this" and first["username"] == "reader"
    assert [item["width"] for item in first["avatar_variants"]] == list(avatar_storage.SIZES)
    assert str(identifier) not in first["avatar_url"]
    assert len(r2.objects) == 5 and all(key.startswith("avatars/") for key in r2.objects)
    keys, old = set(r2.objects), dict(r2.objects)
    response = client.post(
        "/v1/user/settings/profile/avatar",
        files={"file": ("new.png", picture("blue"), "image/png")},
    )
    assert response.status_code == 200
    second = response.json()
    assert first["avatar_url"] != second["avatar_url"]
    assert urlsplit(first["avatar_url"]).path == urlsplit(second["avatar_url"]).path
    assert set(r2.objects) == keys and all(r2.objects[key] != old[key] for key in keys)
    for key, body in r2.objects.items():
        size = int(key.rsplit("/", 1)[1].split(".")[0])
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (size, size) and image.format == "WEBP"
            assert not image.getexif()
    assert all(item["CacheControl"] == avatar_storage.CACHE_CONTROL for item in r2.puts)
    assert all(item["ContentType"] == "image/webp" for item in r2.puts)
    public = client.get("/v1/user/profiles/reader").json()
    assert public["avatar_variants"] == second["avatar_variants"]
    assert "managed_avatar" not in public


@pytest.mark.integration
def test_failed_overwrite_restores_objects_and_database_metadata(
    uploaded_reader, database, monkeypatch
):
    client, r2, _, identifier = uploaded_reader
    first = client.post(
        "/v1/user/settings/profile/avatar", files={"file": ("a.png", picture(), "image/png")}
    ).json()
    old = dict(r2.objects)
    r2.fail_next = f"{avatar_storage.avatar_prefix(identifier)}/128.webp"
    response = client.post(
        "/v1/user/settings/profile/avatar", files={"file": ("b.png", picture("blue"), "image/png")}
    )
    assert response.status_code == 503 and r2.objects == old
    assert client.get("/v1/user/settings/profile").json()["avatar_url"] == first["avatar_url"]
    from sqlalchemy.orm import Session

    with monkeypatch.context() as patch:
        patch.setattr(
            Session, "commit", lambda self: (_ for _ in ()).throw(RuntimeError("commit failed"))
        )
        response = client.post(
            "/v1/user/settings/profile/avatar",
            files={"file": ("c.png", picture("green"), "image/png")},
        )
        assert response.status_code == 500 and r2.objects == old
    with database.begin() as session:
        assert session.get(UserAccount, identifier).profile["avatar_url"] == first["avatar_url"]


@pytest.mark.integration
def test_failed_first_upload_leaves_no_orphan_objects(uploaded_reader):
    client, r2, _, identifier = uploaded_reader
    r2.fail_next = f"{avatar_storage.avatar_prefix(identifier)}/64.webp"
    response = client.post(
        "/v1/user/settings/profile/avatar", files={"file": ("a.png", picture(), "image/png")}
    )
    assert response.status_code == 503 and r2.objects == {}
    assert client.get("/v1/user/settings/profile").json()["avatar_url"] is None


@pytest.mark.integration
def test_remove_and_external_url_replacement_delete_all_owned_objects(uploaded_reader):
    client, r2, _, _ = uploaded_reader
    client.post(
        "/v1/user/settings/profile/avatar", files={"file": ("a.png", picture(), "image/png")}
    )
    response = client.put(
        "/v1/user/settings/profile", json={"avatar_url": "https://example.com/avatar.png"}
    )
    assert response.status_code == 200 and r2.objects == {}
    assert response.json()["avatar_variants"] == []
    client.post(
        "/v1/user/settings/profile/avatar", files={"file": ("a.png", picture(), "image/png")}
    )
    response = client.delete("/v1/user/settings/profile/avatar")
    assert response.status_code == 200 and r2.objects == {}
    assert response.json()["avatar_url"] is None and response.json()["avatar_variants"] == []
    assert client.delete("/v1/user/settings/profile/avatar").status_code == 200


@pytest.mark.integration
def test_invalid_uploads_unowned_identity_and_forged_variants_never_write_storage(uploaded_reader):
    client, r2, user, _ = uploaded_reader
    for body, mime, status in [
        (b"<svg/>", "image/svg+xml", 415),
        (b"not an image", "image/png", 422),
        (b"x" * (avatar_storage.MAX_BYTES + 1), "image/png", 413),
    ]:
        assert (
            client.post(
                "/v1/user/settings/profile/avatar", files={"file": ("file", body, mime)}
            ).status_code
            == status
        )
    assert client.put("/v1/user/settings/profile", json={"avatar_variants": []}).status_code == 422
    user.subject = "other-account"
    assert (
        client.post(
            "/v1/user/settings/profile/avatar", files={"file": ("a.png", picture(), "image/png")}
        ).status_code
        == 401
    )
    assert client.delete("/v1/user/settings/profile/avatar").status_code == 401
    assert r2.objects == {} and r2.puts == []


def test_normalization_strips_exif_and_rejects_excessive_dimensions():
    image = Image.new("RGB", (80, 120), "red")
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "Private camera metadata"
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    for size, body in avatar_storage.normalize_avatar(output.getvalue()).items():
        with Image.open(io.BytesIO(body)) as result:
            assert result.size == (size, size) and not result.getexif()
    with pytest.raises(HTTPException) as error:
        avatar_storage.normalize_avatar(picture(size=(5000, 4001)))
    assert error.value.status_code == 422


def test_upload_configuration_is_required(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(HTTPException) as error:
        avatar_storage.storage_client()
    assert error.value.status_code == 503


def test_chunked_upload_is_bounded_before_form_parsing():
    import asyncio

    from devfeed_user_api.avatars import AvatarUploadBodyLimit

    messages = []

    async def app(scope, receive, send):
        pytest.fail("oversized body reached the parser")

    async def receive():
        return {
            "type": "http.request",
            "body": b"x" * (avatar_storage.MAX_REQUEST_BYTES + 1),
            "more_body": False,
        }

    async def send(message):
        messages.append(message)

    asyncio.run(
        AvatarUploadBodyLimit(app)(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/user/settings/profile/avatar",
                "headers": [(b"content-length", b"1")],
            },
            receive,
            send,
        )
    )
    assert messages[0]["status"] == 413

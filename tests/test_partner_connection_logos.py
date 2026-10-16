"""Import icons are owned, optimized and compensated before readers see managed URLs."""

import io
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import partner_connection_logos as logos
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.models import PartnerAccount, PartnerConnection, PartnerMembership, utcnow
from devfeed_core.object_storage import R2Storage
from devfeed_http import image_uploads
from devfeed_http import partner_connection_logos as uploads
from devfeed_http.partner_portal import creative_preview
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from test_avatar_uploads import FakeR2, picture
from test_partner_account_creatives import shared_catalog as shared_catalog
from test_partner_placement_rotation import issue
from test_partner_portal import portal_client as portal_client
from test_partner_portal_connections import connection_api as connection_api
from test_partner_portal_connections import create

SOURCE = "https://brand.example/icon.png"


@contextmanager
def change_logo(connection, *, session=None, **values):
    prepared = uploads.ConnectionLogoEdit(connection, **values)
    prepared.prepare()
    with prepared.stage(session or Mock(), lambda: None):
        prepared.apply(connection)
        yield


@pytest.fixture
def logo_storage(monkeypatch, request):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example.test")
    get_settings.cache_clear()
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.setattr(uploads, "connection_logo_guard", lambda *_: nullcontext())
    client = FakeR2()
    monkeypatch.setattr(
        image_uploads, "image_upload_storage", lambda: R2Storage("connection-icons", client=client)
    )
    connection = SimpleNamespace(
        provider="test-platform", name="Test platform", logo_url=None, managed_logo={}, revision=1
    )
    return connection, client


@pytest.mark.parametrize(
    "source",
    [
        "http://brand.example/icon.png",
        "https://127.0.0.1/private.png",
        "https://[::1]/private.png",
        "https://10.0.0.1/private.png",
        "https://user:secret@brand.example/icon.png",
        "data:image/png;base64,AA==",
        "https://brand.example/" + "a" * 2048,
    ],
)
def test_logo_sources_reject_nonpublic_unsafe_or_unbounded_inputs(source):
    with pytest.raises(ValidationError):
        logos.ConnectionLogoInput(logo_url=source)


@pytest.mark.parametrize("dimensions", [(300, 50), (50, 300), (2, 2)])
def test_logo_contain_variants_keep_the_whole_mark_in_transparent_square_canvases(dimensions):
    encoded, widths = uploads.ConnectionLogoStorage("test-platform").normalize(
        picture(size=dimensions)
    )
    assert widths == {size: size for size in logos.CONNECTION_LOGO_SIZES}
    for size, body in encoded.items():
        with Image.open(io.BytesIO(body)) as image:
            assert image.size == (size, size) and image.format == "WEBP"
            assert not image.getexif()
            bounds = (
                image.convert("RGBA")
                .getchannel("A")
                .point(lambda value: 255 if value >= 128 else 0)
                .getbbox()
            )
            assert bounds is not None
            width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            if dimensions[0] > dimensions[1]:
                assert width == size and 1 <= height <= round(size / 6) + 2
                assert image.convert("RGBA").getpixel((size // 2, 0))[3] == 0
            elif dimensions[0] < dimensions[1]:
                assert height == size and 1 <= width <= round(size / 6) + 2
                assert image.convert("RGBA").getpixel((0, size // 2))[3] == 0
            else:
                assert (width, height) == (size, size)


def test_url_and_file_share_bounded_versioned_storage_and_clearing_removes_owned_objects(
    logo_storage, monkeypatch
):
    connection, client = logo_storage
    fetch = Mock(
        return_value=SimpleNamespace(
            status=200, content_type="image/png", body=picture(size=(300, 50))
        )
    )
    monkeypatch.setattr(uploads, "_fetch", fetch)
    with change_logo(connection, source_url=SOURCE):
        pass
    first = logos.connection_platform(connection)
    assert connection.logo_url == first.logo_url and connection.revision == 2
    assert first.name == "Test platform" and first.logo_url.startswith(
        "https://images.example.test/"
    )
    assert SOURCE not in first.model_dump_json()
    assert [variant.width for variant in first.logo_variants] == [16, 32, 64, 96]
    assert fetch.call_args.kwargs["max_bytes"] == image_uploads.MAX_BYTES
    assert fetch.call_args.kwargs["timeout"] == 3
    assert fetch.call_args.kwargs["deadline"] > 0
    prefix = logos.connection_logo_prefix(connection.provider)
    assert connection.provider not in prefix
    assert set(client.objects) == {f"{prefix}/{size}.webp" for size in [16, 32, 64, 96]}
    with change_logo(connection, source_url=connection.logo_url):
        pass
    assert connection.revision == 2 and fetch.call_count == 1
    file = SimpleNamespace(content_type="image/png", file=io.BytesIO(picture("blue")))
    with change_logo(connection, file=file):
        pass
    second = logos.connection_platform(connection)
    assert connection.revision == 3 and second.logo_url != first.logo_url
    assert len(client.objects) == 4
    with change_logo(connection, source_url=None):
        pass
    assert connection.logo_url is None and connection.managed_logo == {}
    assert not client.objects and logos.connection_platform(connection).logo_url is None


@pytest.mark.parametrize("failure", ["network", "invalid", "oversize", "mime"])
def test_invalid_logo_replacements_preserve_previous_storage_and_revision(
    logo_storage, monkeypatch, failure
):
    connection, client = logo_storage
    before = connection.revision
    fetch = Mock(return_value=SimpleNamespace(status=200, content_type="image/png", body=picture()))
    monkeypatch.setattr(uploads, "_fetch", fetch)
    if failure == "network":
        fetch.side_effect = FeedError("private upstream diagnostics")
    elif failure == "invalid":
        fetch.return_value.body = b"<svg onload='private'>invalid</svg>"
    elif failure == "oversize":
        fetch.return_value.body = b"x" * (image_uploads.MAX_BYTES + 1)
    values = {"source_url": SOURCE}
    if failure == "mime":
        values = {"file": SimpleNamespace(content_type="text/html", file=io.BytesIO(b"private"))}
    with (
        pytest.raises(HTTPException) as error,
        change_logo(connection, **values),
    ):
        pytest.fail("Invalid image reached publication")
    assert error.value.status_code in {413, 415, 422}
    assert "private" not in error.value.detail
    assert connection.revision == before and connection.logo_url is None
    assert not client.objects


def test_failed_database_publication_restores_existing_logo_bytes(logo_storage, monkeypatch):
    connection, client = logo_storage
    monkeypatch.setattr(
        uploads,
        "_fetch",
        Mock(return_value=SimpleNamespace(status=200, content_type="image/png", body=picture())),
    )
    with change_logo(connection, source_url=SOURCE):
        pass
    previous = dict(client.objects)
    with (
        pytest.raises(RuntimeError, match="publication failed"),
        change_logo(
            connection,
            file=SimpleNamespace(content_type="image/png", file=io.BytesIO(picture("blue"))),
        ),
    ):
        raise RuntimeError("publication failed")
    assert client.objects == previous


def test_reader_logo_presentation_rejects_foreign_or_stale_managed_metadata(
    logo_storage, monkeypatch
):
    connection, _ = logo_storage
    monkeypatch.setattr(
        uploads,
        "_fetch",
        Mock(return_value=SimpleNamespace(status=200, content_type="image/png", body=picture())),
    )
    with change_logo(connection, source_url=SOURCE):
        pass
    connection.provider = "other-platform"
    assert logos.connection_platform(connection).logo_url is None
    connection.provider = "test-platform"
    connection.logo_url = "https://replacement.example/logo.png"
    assert logos.connection_platform(connection).logo_variants == []


def test_omitted_logo_keeps_current_artwork_and_explicit_same_url_repairs_missing_variants(
    logo_storage, monkeypatch
):
    connection, client = logo_storage
    fetch = Mock(return_value=SimpleNamespace(status=200, content_type="image/png", body=picture()))
    monkeypatch.setattr(uploads, "_fetch", fetch)
    connection.logo_url = SOURCE
    with change_logo(connection, source_url=SOURCE):
        pass
    assert fetch.call_count == 1 and logos.connection_platform(connection).logo_url
    before = connection.revision, dict(connection.managed_logo), dict(client.objects)
    writes = len(client.puts)
    with change_logo(connection):
        pass
    assert (connection.revision, connection.managed_logo, client.objects) == before
    assert fetch.call_count == 1 and len(client.puts) == writes


@pytest.mark.parametrize(
    "status,kind,body",
    [(404, "image/png", b"image"), (200, "text/html", b"html"), (200, "image/png", b"")],
)
def test_url_import_requires_successful_nonempty_supported_image_response(
    logo_storage, monkeypatch, status, kind, body
):
    connection, client = logo_storage
    monkeypatch.setattr(
        uploads,
        "_fetch",
        Mock(return_value=SimpleNamespace(status=status, content_type=kind, body=body)),
    )
    with pytest.raises(HTTPException) as error:
        uploads.ConnectionLogoEdit(connection, source_url=SOURCE).prepare()
    assert error.value.status_code == 422 and not client.objects


def test_failed_session_guard_unlock_invalidates_the_connection():
    guard = Mock()
    guard.scalar.side_effect = [True, False]
    context = Mock()
    context.__enter__ = Mock(return_value=guard)
    context.__exit__ = Mock(return_value=False)
    session = Mock()
    session.get_bind.return_value.engine.connect.return_value = context
    with uploads.connection_logo_guard(session, "platform"):
        pass
    guard.invalidate.assert_called_once()


def test_failed_guard_acquisition_commit_invalidates_an_uncertain_session_lock():
    guard = Mock()
    guard.scalar.return_value = True
    guard.commit.side_effect = RuntimeError("connection interrupted after acquisition")
    context = Mock()
    context.__enter__ = Mock(return_value=guard)
    context.__exit__ = Mock(return_value=False)
    session = Mock()
    session.get_bind.return_value.engine.connect.return_value = context
    with (
        pytest.raises(RuntimeError, match="interrupted"),
        uploads.connection_logo_guard(session, "platform"),
    ):
        pytest.fail("Failed acquisition yielded a usable guard")
    guard.invalidate.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize("admin", [False, True])
def test_owned_url_upload_and_clear_api_return_managed_variants_with_revision_fences(
    connection_api, admin_client, logo_storage, monkeypatch, admin
):
    client, _, _, _, base = connection_api
    fetch = Mock(
        return_value=SimpleNamespace(
            status=200, content_type="image/png", body=picture(size=(300, 50))
        )
    )
    monkeypatch.setattr(uploads, "_fetch", fetch)
    connection = create(connection_api, logo_url=SOURCE)
    path = f"{base}/{connection['provider']}"
    if admin:
        client = admin_client
        path = f"/v1/admin/partner-tools/connections/{connection['provider']}"
    assert connection["logo_url"].startswith("https://images.example.test/")
    assert "/64.webp?v=" in connection["logo_url"]
    url_edit = client.put(
        path + "/logo",
        json={
            "logo_url": "https://replacement.example/logo.png",
            "expected_revision": connection["revision"],
        },
    )
    assert url_edit.status_code == 200, url_edit.text
    assert url_edit.json()["name"] == connection["name"]
    assert url_edit.json()["logo_url"] != connection["logo_url"]
    connection = url_edit.json()
    first = client.post(
        path + "/logo",
        data={"expected_revision": str(connection["revision"])},
        files={"file": ("../../logo.png", picture("blue"), "image/png")},
    )
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["revision"] == connection["revision"] + 1
    assert saved["logo_url"] != connection["logo_url"]
    assert len(saved["logo_variants"]) == 4
    before = dict(logo_storage[1].objects)
    stale = client.post(
        path + "/logo",
        data={"expected_revision": str(connection["revision"])},
        files={"file": ("logo.png", picture("green"), "image/png")},
    )
    assert stale.status_code == 409 and logo_storage[1].objects == before
    cleared = client.put(
        path + "/logo", json={"expected_revision": saved["revision"], "logo_url": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["logo_url"] is None and not logo_storage[1].objects


@pytest.mark.integration
@pytest.mark.parametrize("change", ["foreign", "revoked", "paused", "archived"])
def test_logo_upload_rechecks_live_connection_and_membership_scope_before_storage(
    connection_api, database, logo_storage, change
):
    client, identity, account, other, base = connection_api
    connection = create(connection_api)
    with database.begin() as session:
        stored = session.get(PartnerConnection, connection["provider"])
        if change == "foreign":
            stored.account_id = other
        elif change == "revoked":
            session.execute(
                delete(PartnerMembership).where(PartnerMembership.account_id == account)
            )
        elif change == "paused":
            session.get(PartnerAccount, account).status = "paused"
        else:
            stored.archived_at = utcnow()
    response = client.post(
        f"{base}/{connection['provider']}/logo",
        data={"expected_revision": str(connection["revision"])},
        files={"file": ("logo.png", picture(), "image/png")},
    )
    assert response.status_code == 404 and not logo_storage[1].objects


@pytest.mark.integration
def test_delivery_and_preview_platform_follow_own_listing_and_manual_sources_stay_unbranded(
    database, shared_catalog, logo_storage, monkeypatch
):
    catalog = shared_catalog
    monkeypatch.setattr(
        uploads,
        "_fetch",
        Mock(return_value=SimpleNamespace(status=200, content_type="image/png", body=picture())),
    )
    with database() as session:
        connection = session.get(PartnerConnection, "rotation")
        with change_logo(connection, source_url=SOURCE, session=session):
            connection.name = "Owning launch platform"
            session.commit()
        account = session.get(PartnerAccount, catalog["account"])
        preview = creative_preview(session, account, catalog["products"][0]).product
        assert preview.platform.name == "Owning launch platform"
        assert preview.platform.logo_url.startswith("https://images.example.test/")
    delivered = issue(database, catalog)
    assert delivered[0]["partner_name"] == "Rotating sponsor"
    assert delivered[0]["platform"] == preview.platform
    with database.begin() as session:
        from devfeed_core.models import PartnerManualProduct

        session.get(PartnerConnection, "rotation").enabled = False
        session.add(
            PartnerManualProduct(account_id=catalog["account"], product_id=catalog["products"][0])
        )
    manual = issue(database, catalog)
    assert len(manual) == 1 and manual[0]["platform"] is None


@pytest.mark.integration
def test_logo_network_and_compensation_never_hold_catalog_lock_and_provider_guard_survives_rollback(
    connection_api, database, logo_storage, monkeypatch
):
    client, _, _, _, base = connection_api
    connection = create(connection_api)
    provider = connection["provider"]
    path = f"{base}/{provider}"
    phases = []

    def assert_catalog_free(phase):
        with database() as observer:
            assert observer.scalar(select(func.pg_try_advisory_xact_lock(734819025))) is True
            observer.rollback()
        phases.append(phase)

    def fetch(*_args, **_kwargs):
        assert_catalog_free("fetch")
        return SimpleNamespace(status=200, content_type="image/png", body=picture())

    monkeypatch.setattr(uploads, "_fetch", fetch)
    first = client.put(
        path,
        json={"expected_revision": connection["revision"], "enabled": False, "logo_url": SOURCE},
    )
    assert first.status_code == 200, first.text
    before = dict(logo_storage[1].objects)
    revision = first.json()["revision"]
    put = logo_storage[1].put_object

    def observe_write(**values):
        assert_catalog_free("write_or_compensate")
        with database() as contender:
            with (
                pytest.raises(HTTPException) as busy,
                uploads.connection_logo_guard(contender, provider),
            ):
                pytest.fail("Competing provider writer acquired the in-flight guard")
            assert busy.value.status_code == 503
        put(**values)

    monkeypatch.setattr(logo_storage[1], "put_object", observe_write)
    with monkeypatch.context() as patch:
        patch.setattr(
            Session,
            "commit",
            lambda self: (_ for _ in ()).throw(RuntimeError("publication failed")),
        )
        with pytest.raises(RuntimeError, match="publication failed"):
            client.post(
                path + "/logo",
                data={"expected_revision": revision},
                files={"file": ("logo.png", picture("blue"), "image/png")},
            )
    assert logo_storage[1].objects == before
    assert phases.count("write_or_compensate") == 8
    with database() as session:
        stored = session.get(PartnerConnection, provider)
        assert stored.revision == revision and stored.logo_url == first.json()["logo_url"]
        with uploads.connection_logo_guard(session, provider):
            pass


@pytest.mark.integration
def test_membership_revoked_during_download_is_rechecked_before_any_storage_mutation(
    connection_api, database, logo_storage, monkeypatch
):
    client, _, account, _, base = connection_api
    connection = create(connection_api)

    def fetch(*_args, **_kwargs):
        with database.begin() as editor:
            editor.execute(delete(PartnerMembership).where(PartnerMembership.account_id == account))
        return SimpleNamespace(status=200, content_type="image/png", body=picture())

    monkeypatch.setattr(uploads, "_fetch", fetch)
    response = client.put(
        f"{base}/{connection['provider']}",
        json={"expected_revision": connection["revision"], "enabled": False, "logo_url": SOURCE},
    )
    assert response.status_code == 404
    assert not logo_storage[1].objects and not logo_storage[1].puts


@pytest.mark.integration
def test_concurrent_settings_edit_after_storage_staging_rejects_and_restores_logo(
    connection_api, database, logo_storage, monkeypatch
):
    client, _, _, _, base = connection_api
    connection = create(connection_api)
    put = logo_storage[1].put_object
    edited = False

    def edit_after_write(**values):
        nonlocal edited
        put(**values)
        if not edited:
            edited = True
            with database.begin() as editor:
                stored = editor.get(PartnerConnection, connection["provider"])
                stored.name = "Concurrent saved name"
                stored.revision += 1

    monkeypatch.setattr(logo_storage[1], "put_object", edit_after_write)
    response = client.post(
        f"{base}/{connection['provider']}/logo",
        data={"expected_revision": connection["revision"]},
        files={"file": ("logo.png", picture(), "image/png")},
    )
    assert response.status_code == 409 and not logo_storage[1].objects
    with database() as session:
        stored = session.get(PartnerConnection, connection["provider"])
        assert stored.name == "Concurrent saved name" and stored.logo_url is None

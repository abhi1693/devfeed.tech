"""Fixed upload objects retain bounded keys and restore bytes when publication fails."""

import io
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError
from devfeed_http import image_uploads
from fastapi import HTTPException
from pydantic import SecretStr

PREFIX = "partner-ad-images/unit-product"
SIZES = (400, 800, 1200)


class StoredBody(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.puts, self.deletes, self.streams = [], [], []
        self.fail_write = self.fail_delete = None
        self.missing_code = "NoSuchKey"
        self.closed = 0

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": self.missing_code}}, "GetObject")
        stream = StoredBody(self.objects[Key])
        self.streams.append(stream)
        return {"Body": stream}

    def put_object(self, **values):
        self.puts.append(values)
        self.objects[values["Key"]] = values["Body"]
        if values["Key"] == self.fail_write:
            self.fail_write = None
            raise OSError("Timeout after the write reached storage")

    def delete_object(self, *, Bucket, Key):
        self.deletes.append(Key)
        self.objects.pop(Key, None)
        if Key == self.fail_delete:
            self.fail_delete = None
            raise OSError("Timeout after the delete reached storage")

    def close(self):
        self.closed += 1


def upload_settings():
    return SimpleNamespace(
        image_storage_enabled=True,
        image_storage_endpoint="https://storage.example.test",
        image_storage_bucket="private-unit-images",
        image_public_url="https://images.example.test/",
        image_storage_access_key=SecretStr("unit-access-key"),
        image_storage_secret_key=SecretStr("unit-secret-key"),
    )


@pytest.fixture
def storage(monkeypatch):
    settings = upload_settings()
    monkeypatch.setattr(image_uploads, "get_settings", lambda: settings)
    client = MemoryStorage()
    monkeypatch.setattr(image_uploads, "image_upload_client", lambda: client)
    return client, settings


def variants(label):
    return {size: f"{label}-{size}".encode() for size in SIZES}


def owned_objects(label):
    return {f"{PREFIX}/{size}.webp": body for size, body in variants(label).items()}


def test_successful_replacements_reuse_keys_with_new_versions_and_safe_storage_headers(storage):
    client, settings = storage
    with image_uploads.FixedImageObjects(PREFIX, SIZES) as objects:
        first = objects.replace(variants("first"))
    with image_uploads.FixedImageObjects(PREFIX, SIZES, client=client) as objects:
        second = objects.replace(variants("second"), widths={400: 320, 800: 640, 1200: 960})

    assert client.objects == owned_objects("second")
    assert [item["width"] for item in first] == list(SIZES)
    assert [item["width"] for item in second] == [320, 640, 960]
    assert len({urlsplit(item["url"]).query for item in first}) == 1
    for old, new in zip(first, second, strict=True):
        old_url, new_url = urlsplit(old["url"]), urlsplit(new["url"])
        assert old_url.path == new_url.path
        assert old_url.query != new_url.query
        assert len(parse_qs(new_url.query)["v"][0]) == 32
        assert new["url"].startswith(settings.image_public_url)
    assert all("?" not in key for key in client.objects)
    assert all(item["Bucket"] == settings.image_storage_bucket for item in client.puts)
    assert all(item["ContentType"] == "image/webp" for item in client.puts)
    assert all(item["CacheControl"] == image_uploads.CACHE_CONTROL for item in client.puts)
    assert "must-revalidate" in image_uploads.CACHE_CONTROL
    assert all(stream.closed for stream in client.streams)
    assert all(stream.read_sizes == [image_uploads.MAX_BYTES + 1] for stream in client.streams)
    assert client.closed == 2


@pytest.mark.parametrize("existing", [False, True])
def test_partial_write_timeout_restores_old_bytes_and_removes_new_orphans(storage, existing):
    client, _ = storage
    before = owned_objects("old") if existing else {}
    client.objects = dict(before)
    client.fail_write = f"{PREFIX}/800.webp"

    with (
        pytest.raises(HTTPException) as error,
        image_uploads.FixedImageObjects(PREFIX, SIZES) as objects,
    ):
        objects.replace(variants("new"))

    assert error.value.status_code == 503
    assert client.objects == before
    assert not any(
        item["Key"] == f"{PREFIX}/1200.webp" and item["Body"] == b"new-1200" for item in client.puts
    )
    assert client.closed == 1


@pytest.mark.parametrize("existing", [False, True])
def test_failed_database_publication_rolls_back_every_written_object(storage, existing):
    client, _ = storage
    before = owned_objects("old") if existing else {}
    client.objects = dict(before)

    with (
        pytest.raises(RuntimeError, match="Database commit failed"),
        image_uploads.FixedImageObjects(PREFIX, SIZES) as objects,
    ):
        objects.replace(variants("new"))
        assert client.objects == owned_objects("new")
        raise RuntimeError("Database commit failed")

    assert client.objects == before and client.closed == 1


def test_successful_removal_deletes_only_owned_slots(storage):
    client, _ = storage
    unrelated = {"other-account/400.webp": b"preserve"}
    client.objects = {**owned_objects("old"), **unrelated}
    with image_uploads.FixedImageObjects(PREFIX, SIZES) as objects:
        objects.remove()
    assert client.objects == unrelated
    assert set(client.deletes) == set(owned_objects("old"))
    assert client.closed == 1


def test_partial_delete_timeout_restores_removed_slots(storage):
    client, _ = storage
    before = owned_objects("old")
    client.objects = dict(before)
    client.fail_delete = f"{PREFIX}/800.webp"

    with (
        pytest.raises(HTTPException) as error,
        image_uploads.FixedImageObjects(PREFIX, SIZES) as objects,
    ):
        objects.remove()

    assert error.value.status_code == 503
    assert client.objects == before
    assert client.deletes == [f"{PREFIX}/400.webp", f"{PREFIX}/800.webp"]
    assert client.closed == 1


def test_database_failure_after_removal_restores_all_owned_bytes(storage):
    client, _ = storage
    before = owned_objects("old")
    client.objects = dict(before)
    with (
        pytest.raises(RuntimeError, match="Database commit failed"),
        image_uploads.FixedImageObjects(PREFIX, SIZES) as objects,
    ):
        objects.remove()
        assert client.objects == {}
        raise RuntimeError("Database commit failed")
    assert client.objects == before and client.closed == 1


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_absent_object_codes_allow_first_upload(storage, code):
    client, _ = storage
    client.missing_code = code
    with image_uploads.FixedImageObjects(PREFIX, SIZES) as objects:
        objects.replace(variants("first"))
    assert client.objects == owned_objects("first") and client.closed == 1


def test_storage_read_denial_closes_client_without_writing_or_exposing_details(storage):
    client, _ = storage
    client.missing_code = "AccessDenied"
    with pytest.raises(HTTPException) as error, image_uploads.FixedImageObjects(PREFIX, SIZES):
        pytest.fail("Storage read failure allowed publication")
    assert error.value.status_code == 503
    assert error.value.detail == "Image storage is unavailable"
    assert isinstance(error.value.__cause__, ClientError)
    assert client.puts == [] and client.deletes == [] and client.closed == 1


def test_oversized_stored_object_is_bounded_and_closes_stream_and_client(storage, monkeypatch):
    client, _ = storage
    monkeypatch.setattr(image_uploads, "MAX_BYTES", 8)
    client.objects = {f"{PREFIX}/400.webp": b"x" * 9}
    with pytest.raises(HTTPException) as error, image_uploads.FixedImageObjects(PREFIX, SIZES):
        pytest.fail("Oversized rollback snapshot was accepted")
    assert error.value.status_code == 503
    assert client.streams[0].read_sizes == [9] and client.streams[0].closed
    assert client.puts == [] and client.closed == 1


def test_snapshot_read_failure_closes_stream_and_client(storage, monkeypatch):
    client, _ = storage
    client.objects = owned_objects("old")

    def fail_read(self, size=-1):
        raise OSError("Read interrupted")

    monkeypatch.setattr(StoredBody, "read", fail_read)
    with pytest.raises(HTTPException) as error, image_uploads.FixedImageObjects(PREFIX, SIZES):
        pytest.fail("Failed rollback snapshot was accepted")
    assert error.value.status_code == 503
    assert client.streams[0].closed and client.closed == 1
    assert client.puts == []


def test_rollback_failure_logs_safe_category_continues_restoration_and_closes(storage, caplog):
    client, _ = storage
    before = owned_objects("old")
    client.objects = dict(before)
    original_put = client.put_object

    def fail_first_restore(**values):
        if values["Key"] == f"{PREFIX}/400.webp" and values["Body"] == b"old-400":
            raise OSError("Sensitive storage credentials must not appear in logs")
        original_put(**values)

    client.put_object = fail_first_restore
    with (
        pytest.raises(RuntimeError, match="Database commit failed"),
        image_uploads.FixedImageObjects(PREFIX, SIZES) as objects,
    ):
        objects.replace(variants("new"))
        raise RuntimeError("Database commit failed")

    assert client.objects[f"{PREFIX}/800.webp"] == before[f"{PREFIX}/800.webp"]
    assert client.objects[f"{PREFIX}/1200.webp"] == before[f"{PREFIX}/1200.webp"]
    assert "image_upload_rollback_failed" in caplog.text
    assert "Sensitive storage credentials" not in caplog.text
    assert client.closed == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_storage_enabled", False),
        ("image_storage_endpoint", None),
        ("image_storage_bucket", None),
        ("image_public_url", None),
        ("image_storage_access_key", None),
        ("image_storage_secret_key", None),
    ],
)
def test_upload_client_rejects_incomplete_configuration_before_creating_storage(
    monkeypatch, field, value
):
    settings = upload_settings()
    setattr(settings, field, value)
    monkeypatch.setattr(image_uploads, "get_settings", lambda: settings)
    create = Mock()
    monkeypatch.setattr(image_uploads.boto3, "client", create)
    with pytest.raises(HTTPException) as error:
        image_uploads.image_upload_client()
    assert error.value.status_code == 503 and not create.called


def test_upload_client_uses_bounded_transport_without_implicit_retries(monkeypatch):
    settings = upload_settings()
    monkeypatch.setattr(image_uploads, "get_settings", lambda: settings)
    create = Mock()
    monkeypatch.setattr(image_uploads.boto3, "client", create)
    assert image_uploads.image_upload_client() is create.return_value
    options = create.call_args.kwargs
    assert create.call_args.args == ("s3",)
    assert options["endpoint_url"] == settings.image_storage_endpoint
    assert options["region_name"] == "auto"
    assert 0 < options["config"].connect_timeout <= 5
    assert 0 < options["config"].read_timeout <= 5
    assert options["config"].retries == {"total_max_attempts": 1}
    assert options["config"].s3 == {"addressing_style": "path"}

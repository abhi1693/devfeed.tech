"""R2 operations retain bounded transport, exact object scope and owned resource lifetime."""

import builtins
import importlib.util
import io
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from devfeed_core import config, object_storage
from pydantic import SecretStr

BUCKET = "unit-images"
KEY = "originals/unit-image"


class StoredBody(io.BytesIO):
    def __init__(self, body, *, error=None):
        super().__init__(body)
        self.read_sizes = []
        self.bytes_read = 0
        self.error = error

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.error is not None:
            raise self.error
        body = super().read(size)
        self.bytes_read += len(body)
        return body


def storage_settings():
    return SimpleNamespace(
        image_storage_endpoint="https://storage.example.test",
        image_storage_bucket=BUCKET,
        image_storage_access_key=SecretStr("unit-access-key"),
        image_storage_secret_key=SecretStr("unit-secret-key"),
    )


def storage_error(code, status):
    return ClientError(
        {
            "Error": {"Code": code} if code is not None else {},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


@pytest.fixture
def client():
    return Mock(spec=["head_object", "get_object", "put_object", "delete_object", "close"])


@pytest.mark.parametrize("timeouts", [{}, {"connect_timeout": 3, "read_timeout": 5}])
def test_configured_client_uses_explicit_credentials_and_bounded_transport(monkeypatch, timeouts):
    settings = storage_settings()
    create = Mock()
    monkeypatch.setattr(boto3, "client", create)

    storage = object_storage.R2Storage.from_settings(settings, **timeouts)
    storage.exists(KEY)

    assert create.call_args.args == ("s3",)
    options = create.call_args.kwargs
    assert options["endpoint_url"] == settings.image_storage_endpoint
    assert options["region_name"] == "auto"
    assert options["aws_access_key_id"] == "unit-access-key"
    assert options["aws_secret_access_key"] == "unit-secret-key"
    assert options["config"].connect_timeout == timeouts.get("connect_timeout", 5)
    assert options["config"].read_timeout == timeouts.get("read_timeout", 10)
    assert options["config"].retries == {"total_max_attempts": 1}
    assert options["config"].s3 == {"addressing_style": "path"}
    create.return_value.head_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


def test_default_settings_are_resolved_before_creating_the_client(monkeypatch):
    settings = storage_settings()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    create = Mock()
    monkeypatch.setattr(boto3, "client", create)

    object_storage.R2Storage.from_settings().delete(KEY)

    create.return_value.delete_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


@pytest.mark.parametrize(
    "field",
    [
        "image_storage_endpoint",
        "image_storage_bucket",
        "image_storage_access_key",
        "image_storage_secret_key",
    ],
)
@pytest.mark.parametrize("value", [None, ""])
def test_incomplete_configuration_fails_before_sdk_creation(monkeypatch, field, value):
    settings = storage_settings()
    setattr(settings, field, value)
    create = Mock()
    monkeypatch.setattr(boto3, "client", create)

    with pytest.raises(object_storage.R2ConfigurationError):
        object_storage.R2Storage.from_settings(settings)

    create.assert_not_called()


@pytest.mark.parametrize("field", ["image_storage_access_key", "image_storage_secret_key"])
def test_empty_decrypted_credentials_are_rejected_before_sdk_creation(monkeypatch, field):
    settings = storage_settings()
    setattr(settings, field, SecretStr(""))
    create = Mock()
    monkeypatch.setattr(boto3, "client", create)

    with pytest.raises(object_storage.R2ConfigurationError):
        object_storage.R2Storage.from_settings(settings)

    create.assert_not_called()


def test_storage_module_and_invalid_configuration_do_not_require_the_optional_sdk(monkeypatch):
    original_import = builtins.__import__

    def import_without_storage_sdk(name, *args, **kwargs):
        if name == "boto3" or name == "botocore" or name.startswith("botocore."):
            pytest.fail("Incomplete storage configuration imported the optional SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_storage_sdk)
    spec = importlib.util.spec_from_file_location("storage_without_sdk", object_storage.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = storage_settings()
    settings.image_storage_endpoint = None

    with pytest.raises(module.R2ConfigurationError):
        module.R2Storage.from_settings(settings)


def test_exists_targets_only_the_configured_bucket_and_key(client):
    assert object_storage.R2Storage(BUCKET, client=client).exists(KEY) is True
    client.head_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound", None])
@pytest.mark.parametrize("operation", ["exists", "get"])
def test_only_missing_objects_return_absence(client, code, operation):
    error = storage_error(code, 404)
    method = client.head_object if operation == "exists" else client.get_object
    method.side_effect = error
    storage = object_storage.R2Storage(BUCKET, client=client)

    result = storage.exists(KEY) if operation == "exists" else storage.get(KEY, max_bytes=4)

    assert result is (False if operation == "exists" else None)
    method.assert_called_once_with(Bucket=BUCKET, Key=KEY)


@pytest.mark.parametrize(
    "code,status",
    [("AccessDenied", 403), ("InternalError", 500), ("NoSuchBucket", 404), ("UnknownError", 404)],
)
@pytest.mark.parametrize("operation", ["exists", "get"])
def test_denials_dependency_failures_and_missing_buckets_are_not_absent_objects(
    client, code, status, operation
):
    error = storage_error(code, status)
    method = client.head_object if operation == "exists" else client.get_object
    method.side_effect = error
    storage = object_storage.R2Storage(BUCKET, client=client)

    with pytest.raises(ClientError) as caught:
        if operation == "exists":
            storage.exists(KEY)
        else:
            storage.get(KEY, max_bytes=4)

    assert caught.value is error


@pytest.mark.parametrize("body", [b"", b"abcd"])
def test_get_bounds_the_read_and_closes_successful_streams(client, body):
    stream = StoredBody(body)
    client.get_object.return_value = {"Body": stream}

    assert object_storage.R2Storage(BUCKET, client=client).get(KEY, max_bytes=4) == body

    client.get_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)
    assert stream.read_sizes == [5] and stream.closed
    client.close.assert_not_called()


def test_oversized_get_reads_only_one_byte_beyond_the_limit_and_closes(client):
    stream = StoredBody(b"x" * 100)
    client.get_object.return_value = {"Body": stream}

    with pytest.raises(ValueError):
        object_storage.R2Storage(BUCKET, client=client).get(KEY, max_bytes=4)

    assert stream.read_sizes == [5] and stream.bytes_read == 5 and stream.closed
    client.close.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1])
def test_get_rejects_nonpositive_limits_before_opening_a_stream(client, limit):
    with pytest.raises(ValueError):
        object_storage.R2Storage(BUCKET, client=client).get(KEY, max_bytes=limit)

    client.get_object.assert_not_called()


def test_get_closes_a_failed_stream_without_hiding_its_exception(client):
    error = OSError("Read interrupted")
    stream = StoredBody(b"data", error=error)
    client.get_object.return_value = {"Body": stream}

    with pytest.raises(OSError) as caught:
        object_storage.R2Storage(BUCKET, client=client).get(KEY, max_bytes=4)

    assert caught.value is error
    assert stream.read_sizes == [5] and stream.closed


def test_put_preserves_body_content_type_cache_policy_and_bucket_scope(client):
    storage = object_storage.R2Storage(BUCKET, client=client)
    storage.put(KEY, b"image", content_type="image/webp", cache_control="public, immutable")
    storage.delete(KEY)

    client.put_object.assert_called_once_with(
        Bucket=BUCKET,
        Key=KEY,
        Body=b"image",
        ContentType="image/webp",
        CacheControl="public, immutable",
    )
    client.delete_object.assert_called_once_with(Bucket=BUCKET, Key=KEY)


@pytest.mark.parametrize("fails", [False, True])
def test_context_owns_client_closure_without_suppressing_errors(client, fails):
    storage = object_storage.R2Storage(BUCKET, client=client)

    def use_storage():
        with storage as entered:
            assert entered is storage
            if fails:
                raise RuntimeError("Operation failed")

    if fails:
        with pytest.raises(RuntimeError, match="Operation failed"):
            use_storage()
    else:
        use_storage()
    storage.close()
    storage.close()

    client.close.assert_called_once_with()


def test_close_is_idempotent_when_called_before_context_exit(client):
    with object_storage.R2Storage(BUCKET, client=client) as storage:
        storage.close()

    client.close.assert_called_once_with()

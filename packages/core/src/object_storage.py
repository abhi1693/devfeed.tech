"""Bucket-scoped R2 operations; callers own object keys, cache policy and rollback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from botocore.exceptions import ClientError

    from devfeed_core.config import Settings


from types import TracebackType
from typing import Protocol, TypedDict


class ObjectStream(Protocol):
    def read(self, amount: int) -> bytes: ...
    def close(self) -> None: ...


class ObjectResponse(TypedDict):
    ContentLength: int
    Body: ObjectStream


class StorageClient(Protocol):
    def head_object(self, *, Bucket: str, Key: str) -> object: ...
    def get_object(self, *, Bucket: str, Key: str) -> ObjectResponse: ...
    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str, CacheControl: str
    ) -> object: ...
    def delete_object(self, *, Bucket: str, Key: str) -> object: ...
    def close(self) -> None: ...


class R2ConfigurationError(ValueError):
    """The configured R2 endpoint, bucket or credentials are incomplete."""


class R2Storage:
    def __init__(self, bucket: str, *, client: StorageClient) -> None:
        self.bucket = bucket
        self._client = client
        self._closed = False

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        connect_timeout: float = 5,
        read_timeout: float = 10,
    ) -> Self:
        if settings is None:
            from devfeed_core.config import get_settings

            settings = get_settings()
        if not all(
            (
                settings.image_storage_endpoint,
                settings.image_storage_bucket,
                settings.image_storage_access_key,
                settings.image_storage_secret_key,
            )
        ):
            raise R2ConfigurationError("Object storage configuration is incomplete")
        assert settings.image_storage_bucket
        assert settings.image_storage_access_key and settings.image_storage_secret_key
        access_key = settings.image_storage_access_key.get_secret_value()
        secret_key = settings.image_storage_secret_key.get_secret_value()
        if not access_key or not secret_key:
            raise R2ConfigurationError("Object storage configuration is incomplete")

        # The SDK is installed by the core storage extra, only in storage consumers.
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.image_storage_endpoint,
            region_name="auto",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )
        return cls(settings.image_storage_bucket, client=client)

    @staticmethod
    def _missing(error: ClientError) -> bool:
        code = error.response.get("Error", {}).get("Code")
        return code in {"NoSuchKey", "404", "NotFound"} or (
            not code and error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if self._missing(exc):
                return False
            raise
        return True

    def get(self, key: str, *, max_bytes: int) -> bytes | None:
        """Read a bounded object and always release its response stream."""
        from botocore.exceptions import ClientError

        if max_bytes < 1:
            raise ValueError("Object byte limit must be positive")
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if self._missing(exc):
                return None
            raise
        stream = response["Body"]
        try:
            body = stream.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError("Stored object exceeds byte limit")
            return body
        finally:
            stream.close()

    def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            CacheControl=cache_control,
        )

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

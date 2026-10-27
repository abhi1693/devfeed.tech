"""Immutable image storage lifecycles shared by raster and logo policies."""

import base64
import hashlib
import hmac
from abc import ABC, abstractmethod
from typing import TypedDict, Unpack

import httpcore
from devfeed_core.config import Settings, get_settings
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.image_processing import ImageProcessor
from devfeed_core.object_storage import R2Storage


class StoredVariant(TypedDict):
    key: str
    width: int


class ImageMetadata(TypedDict, total=False):
    source_width: int
    original_bytes: int
    thumbnail_version: str
    thumbnail_layout: str
    subject_revision: int
    page_url: str
    page_final_url: str
    discovery_method: str
    icon_url: str


class StoredImage(ImageMetadata):
    source_url: str
    hash: str
    original_key: str
    version: str
    variants: list[StoredVariant]


CACHE_CONTROL = "public, max-age=31536000, immutable"


class ImageStore(ImageProcessor):
    def __init__(self, storage: R2Storage) -> None:
        super().__init__(max_pixels=40_000_000, formats={"JPEG", "PNG", "WEBP", "GIF", "AVIF"})
        self.storage = storage

    def inspect(self, body: bytes) -> tuple[int, str]:
        try:
            return super().inspect(body)
        except ValueError as exc:
            raise FeedError("Unsupported or invalid image", reason="invalid_image") from exc


class ImgproxyTransform:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings if settings is not None else get_settings()

    def url(self, key: str, width: int, *, options: str | None = None) -> str:
        source = f"{self.settings.image_public_url}/{key}"
        encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip("=")
        path = f"/{options or f'rs:fit:{width}:0:0/q:78/f:webp'}/{encoded}"
        assert self.settings.imgproxy_key and self.settings.imgproxy_salt
        secret = bytes.fromhex(self.settings.imgproxy_key.get_secret_value())
        salt = bytes.fromhex(self.settings.imgproxy_salt.get_secret_value())
        signature = (
            base64.urlsafe_b64encode(hmac.digest(secret, salt + path.encode(), "sha256"))
            .decode()
            .rstrip("=")
        )
        return f"{self.settings.imgproxy_url}/{signature}{path}"

    def fetch(self, url: str) -> bytes:
        # Only operator-configured imgproxy URLs reach this transport; sources are DNS-pinned.
        with (
            httpcore.ConnectionPool() as pool,
            pool.stream(
                "GET",
                url,
                extensions={"timeout": dict.fromkeys(["connect", "read", "write", "pool"], 15)},
            ) as response,
        ):
            if response.status != 200:
                raise FeedError(
                    "Image transformation failed",
                    reason="image_transform",
                    retryable=response.status >= 500 or response.status == 429,
                )
            body = bytearray()
            for chunk in response.iter_stream():
                body.extend(chunk)
                if len(body) > self.settings.image_max_bytes:
                    raise FeedError("Thumbnail exceeds limit", reason="image_size")
            return bytes(body)


class OriginalStore(ImageStore, ABC):
    @abstractmethod
    def store(self, source: str) -> StoredImage:
        """Fetch and validate an original using the source's image policy."""

    def _save(
        self,
        source: str,
        body: bytes,
        content_type: str,
        *,
        namespace: str,
        version: str,
        extension: str = "",
        **metadata: Unpack[ImageMetadata],
    ) -> StoredImage:
        digest = hashlib.sha256(body).hexdigest()
        key = f"{namespace}/{digest}{extension}"
        if not self.storage.exists(key):
            self.storage.put(key, body, content_type=content_type, cache_control=CACHE_CONTROL)
        return {
            "source_url": source,
            "hash": digest,
            "original_key": key,
            "version": version,
            "variants": [],
            **metadata,
        }


class VariantStore(ImageStore, ABC):
    def __init__(self, storage: R2Storage, *, transform: ImgproxyTransform | None = None) -> None:
        super().__init__(storage)
        self.transform = transform if transform is not None else ImgproxyTransform()

    def store(self, asset: StoredImage, size: int) -> StoredVariant:
        key = self._key(asset, size)
        if not self.storage.exists(key):
            body = self._render(asset, size)
            self.storage.put(key, body, content_type="image/webp", cache_control=CACHE_CONTROL)
        return {"key": key, "width": size}

    @abstractmethod
    def sizes(self, asset: StoredImage) -> list[int]:
        """Return the subject's bounded variant sizes."""

    @abstractmethod
    def _key(self, asset: StoredImage, size: int) -> str:
        """Validate the encoding policy and choose an immutable object key."""

    @abstractmethod
    def _render(self, asset: StoredImage, size: int) -> bytes:
        """Render and validate a WebP variant before it can be stored."""

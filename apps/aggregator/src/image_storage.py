"""Bounded image transport and R2 storage, called only by the Images pipeline."""

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.managed_images import (
    ARTICLE_THUMBNAIL_WIDTHS,
    IMAGE_VERSION,
    THUMBNAIL_QUALITIES,
    THUMBNAIL_VERSION,
)
from devfeed_core.object_storage import R2ConfigurationError, R2Storage

from devfeed_aggregator.image_storage_base import OriginalStore, StoredImage, VariantStore

WIDTHS = (320, 640, 960)


def storage_client() -> R2Storage:
    settings = get_settings()
    if not all(
        (
            settings.image_public_url,
            settings.imgproxy_url,
            settings.imgproxy_key,
            settings.imgproxy_salt,
        )
    ):
        raise FeedError("Image storage configuration is incomplete", reason="image_configuration")
    try:
        return R2Storage.from_settings(settings)
    except R2ConfigurationError as exc:
        raise FeedError(
            "Image storage configuration is incomplete", reason="image_configuration"
        ) from exc


class ImageOriginalStore(OriginalStore):
    def store(self, source: str) -> StoredImage:
        result = _fetch(
            source,
            None,
            None,
            accept="image/*",
            max_bytes=get_settings().image_max_bytes,
            timeout=15,
            limit_setting="DEVFEED_IMAGE_MAX_BYTES",
        )
        width, mime = self.inspect(result.body)
        return self._save(
            source,
            result.body,
            mime,
            namespace="originals",
            version=IMAGE_VERSION,
            source_width=width,
            original_bytes=len(result.body),
            thumbnail_version=THUMBNAIL_VERSION,
        )


class ThumbnailVariantStore(VariantStore):
    def sizes(self, asset: StoredImage) -> list[int]:
        return sorted({min(width, asset["source_width"]) for width in WIDTHS})

    def _key(self, asset: StoredImage, size: int) -> str:
        # Checkpointed v1 jobs retain their encoder and immutable object namespace.
        version = asset.get("thumbnail_version", "v1")
        if version not in THUMBNAIL_QUALITIES:
            raise FeedError("Unknown thumbnail encoding", reason="image_transform", retryable=False)
        return f"thumbnails/{version}/{asset['hash']}/{size}.webp"

    def _render(self, asset: StoredImage, size: int) -> bytes:
        quality = THUMBNAIL_QUALITIES[asset.get("thumbnail_version", "v1")]
        body = self.transform.fetch(
            self.transform.url(
                asset["original_key"], size, options=f"rs:fit:{size}:0:0/q:{quality}/f:webp"
            )
        )
        actual_width, mime = self.inspect(body)
        if mime != "image/webp" or actual_width != size:
            raise FeedError("Invalid transformed image", reason="image_transform")
        return body


class ArticleThumbnailVariantStore(ThumbnailVariantStore):
    """Fill common card/DPR gaps without changing product images or legacy jobs."""

    def sizes(self, asset: StoredImage) -> list[int]:
        widths = ARTICLE_THUMBNAIL_WIDTHS if asset.get("thumbnail_version") == "v2" else WIDTHS
        return sorted({min(width, asset["source_width"]) for width in widths})

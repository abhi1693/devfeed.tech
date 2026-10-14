"""Bounded raster uploads and rollback-capable fixed object storage for private APIs."""

import io
import logging
from threading import BoundedSemaphore
from uuid import uuid4

from devfeed_core.config import get_settings
from devfeed_core.image_processing import ImageProcessor
from devfeed_core.object_storage import R2ConfigurationError, R2Storage
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_BYTES + 65536
MAX_PIXELS = 20_000_000
CACHE_CONTROL = "public, max-age=0, must-revalidate"
logger = logging.getLogger(__name__)
_normalization_slot = BoundedSemaphore(1)


def image_upload_storage() -> R2Storage:
    settings = get_settings()
    if not settings.image_storage_enabled or not settings.image_public_url:
        raise HTTPException(503, "Image uploads are unavailable")
    try:
        return R2Storage.from_settings(settings, connect_timeout=3, read_timeout=5)
    except R2ConfigurationError:
        raise HTTPException(503, "Image uploads are unavailable") from None


class ImageUploadStorage(ImageProcessor):
    """Normalize one upload policy and compensate fixed-key changes if publication fails."""

    def __init__(self, prefix, sizes, *, square=False, storage: R2Storage | None = None):
        super().__init__(max_pixels=MAX_PIXELS, formats={"JPEG", "PNG", "WEBP"}, orient_width=False)
        self.storage = storage
        self.prefix, self.sizes, self.square = prefix, tuple(sizes), square
        self.previous: dict[str, bytes | None] = {}
        self.changed: list[str] = []
        self.revision = uuid4().hex

    def normalize(self, body: bytes):
        if not _normalization_slot.acquire(blocking=False):
            raise HTTPException(
                503, "Image processing is busy; try again shortly", headers={"Retry-After": "1"}
            )
        try:
            return self._normalize(body)
        finally:
            _normalization_slot.release()

    def _normalize(self, body: bytes):
        if len(body) > MAX_BYTES:
            raise HTTPException(413, "Choose an image under 5 MB")
        try:
            self.inspect(body)
            with Image.open(io.BytesIO(body)) as image:
                ImageOps.exif_transpose(image, in_place=True)
                if self.square:
                    reduced = ImageOps.fit(
                        image, (max(self.sizes), max(self.sizes)), Image.Resampling.LANCZOS
                    )
                else:
                    image.thumbnail((max(self.sizes), max(self.sizes)), Image.Resampling.LANCZOS)
                    reduced = image
                master = reduced.convert("RGBA")
                result, widths = {}, {}
                for size in self.sizes:
                    if self.square:
                        variant = ImageOps.fit(master, (size, size), Image.Resampling.LANCZOS)
                    else:
                        variant = master.copy()
                        variant.thumbnail((size, max(self.sizes)), Image.Resampling.LANCZOS)
                    result[size] = self.encode(
                        variant, format="WEBP", strip_metadata=True, quality=85, method=4
                    )
                    widths[size] = variant.width
                return result, widths
        except (
            ValueError,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise HTTPException(
                422, "Choose a valid JPEG, PNG or WebP image up to 20 megapixels"
            ) from exc

    def __enter__(self):
        if self.storage is None:
            self.storage = image_upload_storage()
        try:
            for size in self.sizes:
                key = f"{self.prefix}/{size}.webp"
                self.previous[key] = self.storage.get(key, max_bytes=MAX_BYTES)
            return self
        except Exception as exc:
            self.storage.close()
            raise HTTPException(503, "Image storage is unavailable") from exc

    def put(self, key, body):
        assert self.storage is not None
        self.storage.put(key, body, content_type="image/webp", cache_control=CACHE_CONTROL)

    def replace(self, variants, *, widths=None):
        base = (get_settings().image_public_url or "").rstrip("/")
        urls = []
        try:
            for size, body in variants.items():
                key = f"{self.prefix}/{size}.webp"
                self.changed.append(key)
                self.put(key, body)
                urls.append(
                    {
                        "width": widths[size] if widths else size,
                        "url": f"{base}/{key}?v={self.revision}",
                    }
                )
        except Exception as exc:
            raise HTTPException(503, "Couldn’t upload the image. Try again") from exc
        return urls

    def remove(self):
        assert self.storage is not None
        try:
            for key in self.previous:
                self.changed.append(key)
                self.storage.delete(key)
        except Exception as exc:
            raise HTTPException(503, "Couldn’t remove the image. Try again") from exc

    def __exit__(self, exception_type, exception, traceback):
        assert self.storage is not None
        try:
            if exception_type is not None:
                for key in self.changed:
                    try:
                        if self.previous[key] is None:
                            self.storage.delete(key)
                        else:
                            self.put(key, self.previous[key])
                    except Exception:
                        logger.error("image_upload_rollback_failed")
        finally:
            self.storage.close()

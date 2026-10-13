"""Bounded raster uploads and rollback-capable fixed object storage for private APIs."""

import asyncio
import io
import logging
import warnings
from threading import BoundedSemaphore
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from devfeed_core.config import get_settings
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.responses import JSONResponse

MAX_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_BYTES + 65536
MAX_PIXELS = 20_000_000
CACHE_CONTROL = "public, max-age=0, must-revalidate"
logger = logging.getLogger(__name__)
_normalization_slot = BoundedSemaphore(1)


def normalize_image(body: bytes, sizes, *, square=False):
    if not _normalization_slot.acquire(blocking=False):
        raise HTTPException(
            503, "Image processing is busy; try again shortly", headers={"Retry-After": "1"}
        )
    try:
        return _normalize_image(body, sizes, square=square)
    finally:
        _normalization_slot.release()


def _normalize_image(body: bytes, sizes, *, square=False):
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "Choose an image under 5 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Unsupported format")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image dimensions too large")
                image.verify()
            with Image.open(io.BytesIO(body)) as image:
                ImageOps.exif_transpose(image, in_place=True)
                if square:
                    reduced = ImageOps.fit(
                        image, (max(sizes), max(sizes)), Image.Resampling.LANCZOS
                    )
                else:
                    image.thumbnail((max(sizes), max(sizes)), Image.Resampling.LANCZOS)
                    reduced = image
                master = reduced.convert("RGBA")
                result, widths = {}, {}
                for size in sizes:
                    if square:
                        variant = ImageOps.fit(master, (size, size), Image.Resampling.LANCZOS)
                    else:
                        variant = master.copy()
                        variant.thumbnail((size, max(sizes)), Image.Resampling.LANCZOS)
                    variant.info.clear()
                    output = io.BytesIO()
                    variant.save(output, format="WEBP", quality=85, method=4)
                    result[size], widths[size] = output.getvalue(), variant.width
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


def image_upload_client():
    settings = get_settings()
    if not settings.image_storage_enabled or not all(
        (
            settings.image_storage_endpoint,
            settings.image_storage_bucket,
            settings.image_public_url,
            settings.image_storage_access_key,
            settings.image_storage_secret_key,
        )
    ):
        raise HTTPException(503, "Image uploads are unavailable")
    assert settings.image_storage_access_key and settings.image_storage_secret_key
    return boto3.client(
        "s3",
        endpoint_url=settings.image_storage_endpoint,
        region_name="auto",
        aws_access_key_id=settings.image_storage_access_key.get_secret_value(),
        aws_secret_access_key=settings.image_storage_secret_key.get_secret_value(),
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


class FixedImageObjects:
    """Snapshot fixed keys in memory so partial storage writes and failed DB commits roll back."""

    def __init__(self, prefix, sizes, *, client=None):
        self.client = client if client is not None else image_upload_client()
        self.bucket = get_settings().image_storage_bucket
        self.prefix, self.sizes = prefix, sizes
        self.previous: dict[str, bytes | None] = {}
        self.changed: list[str] = []
        self.revision = uuid4().hex

    def __enter__(self):
        try:
            for size in self.sizes:
                key = f"{self.prefix}/{size}.webp"
                try:
                    response = self.client.get_object(Bucket=self.bucket, Key=key)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") in {
                        "NoSuchKey",
                        "404",
                        "NotFound",
                    }:
                        self.previous[key] = None
                        continue
                    raise
                stream = response["Body"]
                try:
                    body = stream.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise ValueError("Oversized stored image")
                    self.previous[key] = body
                finally:
                    stream.close()
            return self
        except Exception as exc:
            self.client.close()
            raise HTTPException(503, "Image storage is unavailable") from exc

    def put(self, key, body):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="image/webp",
            CacheControl=CACHE_CONTROL,
        )

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
        try:
            for key in self.previous:
                self.changed.append(key)
                self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise HTTPException(503, "Couldn’t remove the image. Try again") from exc

    def __exit__(self, exception_type, exception, traceback):
        try:
            if exception_type is not None:
                for key in self.changed:
                    try:
                        if self.previous[key] is None:
                            self.client.delete_object(Bucket=self.bucket, Key=key)
                        else:
                            self.put(key, self.previous[key])
                    except Exception:
                        logger.error("image_upload_rollback_failed")
        finally:
            self.client.close()


class ImageUploadBodyLimit:
    """Limit bytes and total reading time before multipart parsing allocates or spools."""

    def __init__(self, app, *, paths=(), path_predicate=None, timeout=10):
        self.app, self.paths, self.path_predicate, self.timeout = (
            app,
            paths,
            path_predicate,
            timeout,
        )

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not (path in self.paths or self.path_predicate and self.path_predicate(path))
        ):
            return await self.app(scope, receive, send)
        try:
            declared = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > MAX_REQUEST_BYTES:
            return await self.reject(scope, receive, send, 413)
        chunks, size = [], 0
        try:
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > MAX_REQUEST_BYTES:
                        return await self.reject(scope, receive, send, 413)
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await self.reject(scope, receive, send, 408)
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def reject(self, scope, receive, send, status):
        detail = "Choose an image under 5 MB" if status == 413 else "Image upload timed out"
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

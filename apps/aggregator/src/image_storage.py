"""Bounded image transport and R2 storage, called only by the Images pipeline."""

import base64
import hashlib
import hmac
import io
import warnings

import boto3
import httpcore
from botocore.config import Config
from botocore.exceptions import ClientError
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.managed_images import IMAGE_VERSION
from PIL import Image, UnidentifiedImageError

WIDTHS = (320, 640, 960)
CACHE_CONTROL = "public, max-age=31536000, immutable"


def storage_client():
    settings = get_settings()
    if not all(
        (
            settings.image_storage_endpoint,
            settings.image_storage_bucket,
            settings.image_public_url,
            settings.image_storage_access_key,
            settings.image_storage_secret_key,
            settings.imgproxy_url,
            settings.imgproxy_key,
            settings.imgproxy_salt,
        )
    ):
        raise FeedError("Image storage configuration is incomplete", reason="image_configuration")
    assert settings.image_storage_access_key and settings.image_storage_secret_key
    return boto3.client(
        "s3",
        endpoint_url=settings.image_storage_endpoint,
        region_name="auto",
        aws_access_key_id=settings.image_storage_access_key.get_secret_value(),
        aws_secret_access_key=settings.image_storage_secret_key.get_secret_value(),
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


def inspect_image(body: bytes) -> tuple[int, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as image:
                if image.width * image.height > 40_000_000 or image.format not in {
                    "JPEG",
                    "PNG",
                    "WEBP",
                    "GIF",
                    "AVIF",
                }:
                    raise ValueError("Unsupported image")
                width = image.height if image.getexif().get(274) in {5, 6, 7, 8} else image.width
                mime = Image.MIME[image.format]
            # Reading PNG EXIF may load its stream; verify requires a freshly opened image.
            with Image.open(io.BytesIO(body)) as image:
                image.verify()
            return width, mime
    except (
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise FeedError("Unsupported or invalid image", reason="invalid_image") from exc


def exists(client, key: str) -> bool:
    try:
        client.head_object(Bucket=get_settings().image_storage_bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["ResponseMetadata"]["HTTPStatusCode"] == 404:
            return False
        raise


def upload(client, key: str, body: bytes, mime: str):
    client.put_object(
        Bucket=get_settings().image_storage_bucket,
        Key=key,
        Body=body,
        ContentType=mime,
        CacheControl=CACHE_CONTROL,
    )


def store_original(client, source: str) -> dict:
    settings = get_settings()
    result = _fetch(
        source,
        None,
        None,
        accept="image/*",
        max_bytes=settings.image_max_bytes,
        timeout=15,
        limit_setting="DEVFEED_IMAGE_MAX_BYTES",
    )
    width, mime = inspect_image(result.body)
    digest = hashlib.sha256(result.body).hexdigest()
    key = f"originals/{digest}"
    if not exists(client, key):
        upload(client, key, result.body, mime)
    return {
        "source_url": source,
        "hash": digest,
        "original_key": key,
        "source_width": width,
        "original_bytes": len(result.body),
        "version": IMAGE_VERSION,
        "variants": [],
    }


def signed_transform_url(key: str, width: int) -> str:
    settings = get_settings()
    source = f"{settings.image_public_url}/{key}"
    encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip("=")
    path = f"/rs:fit:{width}:0:0/q:78/f:webp/{encoded}"
    assert settings.imgproxy_key and settings.imgproxy_salt
    secret = bytes.fromhex(settings.imgproxy_key.get_secret_value())
    salt = bytes.fromhex(settings.imgproxy_salt.get_secret_value())
    signature = (
        base64.urlsafe_b64encode(hmac.digest(secret, salt + path.encode(), "sha256"))
        .decode()
        .rstrip("=")
    )
    return f"{settings.imgproxy_url}/{signature}{path}"


def store_variant(client, asset: dict, width: int) -> dict:
    key = f"thumbnails/{IMAGE_VERSION}/{asset['hash']}/{width}.webp"
    if not exists(client, key):
        # This origin is operator-configured; publisher URLs only use DNS-pinned _fetch.
        with (
            httpcore.ConnectionPool() as pool,
            pool.stream(
                "GET",
                signed_transform_url(asset["original_key"], width),
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
                if len(body) > get_settings().image_max_bytes:
                    raise FeedError("Thumbnail exceeds limit", reason="image_size")
        actual_width, mime = inspect_image(bytes(body))
        if mime != "image/webp" or actual_width != width:
            raise FeedError("Invalid transformed image", reason="image_transform")
        upload(client, key, bytes(body), mime)
    return {"key": key, "width": width}


def variant_widths(asset: dict) -> list[int]:
    return sorted({min(width, asset["source_width"]) for width in WIDTHS})

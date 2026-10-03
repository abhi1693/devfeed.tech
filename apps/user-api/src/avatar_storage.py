"""Fixed per-account avatar objects; replacement never creates historical keys."""

import hashlib
import io
import logging
import warnings
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from devfeed_core.config import get_settings
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

SIZES = (32, 64, 128, 256, 512)
MAX_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_BYTES + 65536  # Multipart envelope, filenames and headers.
MAX_PIXELS = 20_000_000
CACHE_CONTROL = "public, max-age=0, must-revalidate"
logger = logging.getLogger(__name__)


def avatar_prefix(account_id) -> str:
    # Public object URLs do not reveal the private account UUID.
    return f"avatars/{hashlib.sha256(str(account_id).encode()).hexdigest()}"


def normalize_avatar(body: bytes) -> dict[int, bytes]:
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
                # Normalize orientation, freeze animated WebP at its first frame,
                # and strip EXIF/location metadata. Only square WebP files are stored.
                master = ImageOps.exif_transpose(image).convert("RGBA")
                result = {}
                for size in SIZES:
                    variant = ImageOps.fit(master, (size, size), Image.Resampling.LANCZOS)
                    variant.info.clear()
                    output = io.BytesIO()
                    variant.save(output, format="WEBP", quality=85, method=4)
                    result[size] = output.getvalue()
                return result
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


def storage_client():
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
        raise HTTPException(503, "Avatar uploads are unavailable")
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


class AvatarObjects:
    """Keep rollback bytes only in memory while R2 writes and the DB commit finish."""

    def __init__(self, account_id):
        self.client = storage_client()
        self.bucket = get_settings().image_storage_bucket
        self.prefix = avatar_prefix(account_id)
        self.previous: dict[str, bytes | None] = {}
        self.changed: list[str] = []

    def __enter__(self):
        try:
            for size in SIZES:
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
                    body = stream.read(1_000_001)
                    if len(body) > 1_000_000:
                        raise ValueError("Oversized stored avatar")
                    self.previous[key] = body
                finally:
                    stream.close()
            return self
        except Exception as exc:
            self.client.close()
            raise HTTPException(503, "Avatar storage is unavailable") from exc

    def put(self, key, body):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="image/webp",
            CacheControl=CACHE_CONTROL,
        )

    def replace(self, variants):
        revision = uuid4().hex
        base = (get_settings().image_public_url or "").rstrip("/")
        urls = []
        try:
            for size, body in variants.items():
                key = f"{self.prefix}/{size}.webp"
                self.changed.append(key)  # Include writes that time out after reaching R2.
                self.put(key, body)
                urls.append({"width": size, "url": f"{base}/{key}?v={revision}"})
        except Exception as exc:
            raise HTTPException(503, "Couldn’t upload your avatar. Try again") from exc
        return urls

    def remove(self):
        try:
            for key in self.previous:
                self.changed.append(key)
                self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise HTTPException(503, "Couldn’t remove your avatar. Try again") from exc

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
                        logger.error("avatar_rollback_failed")
        finally:
            self.client.close()

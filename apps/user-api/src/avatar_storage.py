"""Profile-specific square variants over shared bounded upload storage."""

import hashlib

from devfeed_http.image_uploads import (
    FixedImageObjects,
    image_upload_client,
    normalize_image,
)

SIZES = (32, 64, 128, 256, 512)


def avatar_prefix(account_id) -> str:
    return f"avatars/{hashlib.sha256(str(account_id).encode()).hexdigest()}"


def normalize_avatar(body: bytes) -> dict[int, bytes]:
    variants, _ = normalize_image(body, SIZES, square=True)
    return variants


def storage_client():
    return image_upload_client()


class AvatarObjects(FixedImageObjects):
    def __init__(self, account_id):
        super().__init__(avatar_prefix(account_id), SIZES, client=storage_client())

"""Profile-specific square variants over shared bounded upload storage."""

import hashlib

from devfeed_core.object_storage import R2Storage
from devfeed_http.image_uploads import ImageUploadStorage


class AvatarStorage(ImageUploadStorage):
    """An account's fixed square avatar slots and reversible upload lifecycle."""

    sizes = (32, 64, 128, 256, 512)

    def __init__(self, account_id, *, storage: R2Storage | None = None):
        prefix = f"avatars/{hashlib.sha256(str(account_id).encode()).hexdigest()}"
        super().__init__(prefix, self.sizes, square=True, storage=storage)

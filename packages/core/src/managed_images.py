"""Reader metadata only: serving an image never performs storage or proxy I/O."""

from __future__ import annotations

from typing import TypedDict

from devfeed_core.config import get_settings
from devfeed_core.models import Article

IMAGE_VERSION = "v1"
THUMBNAIL_VERSION = "v2"
THUMBNAIL_QUALITIES = {"v1": 78, "v2": 70}
ARTICLE_THUMBNAIL_WIDTHS = (320, 480, 640, 768, 960)
ARTICLE_THUMBNAIL_LAYOUT = "cards-v1"


class ImageVariant(TypedDict):
    url: str
    width: int


def image_variants(article: Article) -> list[ImageVariant]:
    settings = get_settings()
    asset = article.managed_image or {}
    if (
        not settings.image_storage_enabled
        or not settings.image_public_url
        or asset.get("source_url") != article.image_url
        or asset.get("version") != IMAGE_VERSION
    ):
        return []
    return [
        {"url": f"{settings.image_public_url}/{item['key']}", "width": item["width"]}
        for item in asset.get("variants", [])
    ]

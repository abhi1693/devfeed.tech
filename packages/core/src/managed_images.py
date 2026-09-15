"""Reader metadata only: serving an image never performs storage or proxy I/O."""

from devfeed_core.config import get_settings

IMAGE_VERSION = "v1"


def image_variants(article) -> list[dict]:
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

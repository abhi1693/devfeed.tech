"""Discover official product icons with the normal bounded, DNS-pinned image fetcher."""

from devfeed_core.feeds.fetcher import FeedError, fetch_page
from devfeed_core.partner_product_logos import product_icon_urls

from devfeed_aggregator.image_storage_base import StoredImage
from devfeed_aggregator.logo_storage import LogoOriginalStore


class ProductLogoOriginalStore(LogoOriginalStore):
    def store(self, source: str) -> StoredImage:
        page = fetch_page(source)
        if page.status != 200 or not page.body:
            raise FeedError("Product site unavailable", reason="invalid_product_logo")
        error = None
        for icon in product_icon_urls(page):
            try:
                asset = super().store(icon)
                return {**asset, "source_url": source, "icon_url": icon}
            except FeedError as exc:
                if exc.retryable:
                    raise
                error = exc
        raise error or FeedError("Product icon not found", reason="invalid_product_logo")

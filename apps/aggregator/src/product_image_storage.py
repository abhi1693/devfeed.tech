"""Discover product Open Graph images and store them through article image storage."""

from devfeed_core.feeds.fetcher import FeedError, fetch_page
from devfeed_core.images import extract_image

from devfeed_aggregator.image_storage import store_original


def store_product_image_original(client, source):
    page = fetch_page(source)
    if page.status != 200 or not page.body:
        raise FeedError("Product page unavailable", reason="invalid_product_page")
    image = extract_image(page)
    if image is None:
        raise FeedError("Product preview image absent", reason="product_image_not_found")
    return {
        **store_original(client, image.url),
        "page_url": source,
        "page_final_url": page.final_url,
        "discovery_method": image.method,
    }

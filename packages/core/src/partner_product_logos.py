"""Official-site icon discovery and managed product-logo presentation."""

from html.parser import HTMLParser
from urllib.parse import urljoin

from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.images import decode_html, image_url
from devfeed_core.logos import logo_url
from devfeed_core.models import PartnerProduct


def product_logo_url(product: PartnerProduct) -> str | None:
    asset = getattr(product, "managed_logo", None) or {}
    if not asset or asset.get("source_url") != product.product_url:
        return None
    return logo_url(product)


class ProductIconParser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.icons: list[tuple[int, str]] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"template", "noscript"}:
            self.ignored += 1
        if self.ignored or tag != "link" or len(self.icons) >= 20:
            return
        values = dict(attrs)
        rel = set((values.get("rel") or "").lower().split())
        if not rel.intersection({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}):
            return
        url = image_url(values.get("href"), self.base)
        if url and url.startswith("https://"):
            # Touch icons are usually larger and more legible than legacy favicons.
            self.icons.append((0 if "apple-touch-icon" in rel else 1, url))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"template", "noscript"}:
            self.ignored = max(0, self.ignored - 1)


def product_icon_urls(result: FetchResult) -> list[str]:
    parser = ProductIconParser(result.final_url)
    parser.feed(decode_html(result))
    fallback = image_url(urljoin(result.final_url, "/favicon.ico"), result.final_url)
    urls = [url for _, url in sorted(parser.icons, key=lambda item: item[0])]
    if fallback and fallback.startswith("https://"):
        urls.append(fallback)
    return list(dict.fromkeys(urls))[:3]

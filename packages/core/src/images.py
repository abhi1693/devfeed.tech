"""Source-independent preview image discovery; never execute scripts or fetch images."""

import json
import re
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import urljoin

from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.urls import validate_public_url


@dataclass(frozen=True)
class PageImage:
    url: str
    method: str


def image_url(value, base: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # Preserve query strings, including signed CDN URLs; never canonicalize images.
        return validate_public_url(urljoin(base, value.strip()))
    except ValueError:
        return None


class ImageParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.base_seen = False
        self.meta: dict[str, list[str]] = {}
        self.opengraph: list[dict[str, str]] = []
        self.documents: list[str] = []
        self.script: list[str] | None = None
        self.script_size = 0
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"template", "noscript"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "base" and not self.base_seen:
            self.base_seen = True
            self.base = image_url(values.get("href"), self.base) or self.base
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower().strip()
            if key in {
                "twitter:image",
                "twitter:image:src",
            }:
                candidates = self.meta.setdefault(key, [])
                if len(candidates) < 30 and values.get("content"):
                    candidates.append(values["content"][:4096])
            elif key in {"og:image", "og:image:url", "og:image:secure_url"} and values.get(
                "content"
            ):
                if key == "og:image:secure_url" and self.opengraph:
                    self.opengraph[-1].setdefault("secure", values["content"][:4096])
                elif len(self.opengraph) < 30:
                    self.opengraph.append({"url": values["content"][:4096], "method": key})
        elif (
            tag == "script" and (values.get("type") or "").lower().strip() == "application/ld+json"
        ):
            if len(self.documents) < 20:
                self.script = []
                self.script_size = 0

    def handle_data(self, data):
        if self.script is not None:
            self.script_size += len(data)
            if self.script_size <= 200_000:
                self.script.append(data)
            else:
                self.script = None

    def handle_endtag(self, tag):
        if tag in {"template", "noscript"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "script" and self.script is not None:
            self.documents.append("".join(self.script))
            self.script = None


def decode_html(result: FetchResult) -> str:
    header = Message()
    header["content-type"] = result.content_type or "text/html"
    charset = header.get_content_charset()
    if not charset:
        match = re.search(rb"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", result.body[:4096])
        charset = match[1].decode("ascii") if match else "utf-8-sig"
    try:
        return result.body.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return result.body.decode("utf-8-sig", errors="replace")


def structured_images(documents: list[str]):
    """Only article/page image properties, not organization logos or author portraits."""
    for document in documents:
        try:
            root = json.loads(document)
        except (ValueError, RecursionError):
            continue
        pending = [(root, 0)]
        visited = 0
        nodes = []
        while pending and visited < 1000:
            node, depth = pending.pop()
            visited += 1
            if depth > 12:
                continue
            if isinstance(node, list):
                pending.extend((item, depth + 1) for item in reversed(node[:100]))
            elif isinstance(node, dict):
                nodes.append(node)
                for key in ("@graph", "mainEntity"):
                    if key in node:
                        pending.append((node[key], depth + 1))
        references = {node["@id"]: node for node in nodes if isinstance(node.get("@id"), str)}
        for kind in ("article", "page"):
            for node in nodes:
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if not isinstance(types, list):
                    continue
                types = {item.rsplit("/", 1)[-1] for item in types if isinstance(item, str)}
                eligible = (
                    bool(types & {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report"})
                    if kind == "article"
                    else "WebPage" in types
                )
                if not eligible:
                    continue
                value = node.get("image") or node.get("primaryImageOfPage")
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, dict):
                        reference = item.get("@id")
                        if isinstance(reference, str):
                            item = references.get(reference, item)
                        yield item.get("contentUrl") or item.get("url")
                    else:
                        yield item


def extract_image(result: FetchResult) -> PageImage | None:
    parser = ImageParser(result.final_url)
    parser.feed(decode_html(result))
    parser.close()
    # Explicit preview metadata only; a random <img> may be a logo, advert or tracking pixel.
    for group in parser.opengraph:
        secure = image_url(group.get("secure"), parser.base)
        if secure and secure.startswith("https:"):
            return PageImage(secure, "og:image:secure_url")
        if url := image_url(group["url"], parser.base):
            return PageImage(url, group["method"])
    for key in ("twitter:image", "twitter:image:src"):
        for candidate in parser.meta.get(key, []):
            if url := image_url(candidate, parser.base):
                return PageImage(url, key)
    for candidate in structured_images(parser.documents):
        if url := image_url(candidate, parser.base):
            return PageImage(url, "json_ld")
    return None

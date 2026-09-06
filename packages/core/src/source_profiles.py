"""Bounded source profile metadata; publishers are not submitter identities."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.images import decode_html, extract_image, image_url

PROFILE_FIELDS = ("description", "website_url", "logo_url", "image_url", "language")


def language_code(value) -> str | None:
    value = str(value or "").strip().lower()
    return (
        value
        if len(value) <= 35 and re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", value)
        else None
    )


@dataclass(frozen=True)
class SourceProfile:
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    language: str | None = None


def feed_profile(feed, base_url: str) -> SourceProfile:
    # Local import avoids a parser/profile cycle; the parser's text cleaner is shared.
    from devfeed_core.feeds.parser import plain_text

    image = feed.get("image")
    image = image if isinstance(image, dict) else {}
    return SourceProfile(
        description=plain_text(str(feed.get("subtitle") or feed.get("description") or ""), 500)
        or None,
        website_url=image_url(feed.get("link"), base_url),
        logo_url=image_url(
            feed.get("logo") or image.get("href") or image.get("url") or feed.get("icon"), base_url
        ),
        language=language_code(feed.get("language")),
    )


class WebsiteProfileParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.base_seen = False
        self.descriptions: dict[str, str] = {}
        self.icons: list[tuple[int, str]] = []
        self.language: str | None = None
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"template", "noscript"}:
            self.suppressed += 1
        if self.suppressed:
            return
        if tag == "html":
            self.language = language_code(values.get("lang") or values.get("xml:lang"))
        elif tag == "base" and not self.base_seen:
            self.base_seen = True
            self.base = image_url(values.get("href"), self.base) or self.base
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key in {"description", "og:description", "twitter:description"} and values.get(
                "content"
            ):
                self.descriptions.setdefault(key, values["content"][:10000])
        elif tag == "link" and values.get("href") and len(self.icons) < 30:
            rels = (values.get("rel") or "").lower().split()
            if "apple-touch-icon" in rels:
                self.icons.append((0, values["href"]))
            elif "icon" in rels:
                self.icons.append((1, values["href"]))

    def handle_endtag(self, tag):
        if tag in {"template", "noscript"}:
            self.suppressed = max(0, self.suppressed - 1)


def website_profile(result: FetchResult) -> SourceProfile:
    from devfeed_core.feeds.parser import plain_text

    parser = WebsiteProfileParser(result.final_url)
    parser.feed(decode_html(result))
    parser.close()
    description = next(
        (
            plain_text(parser.descriptions[key], 500)
            for key in ("description", "og:description", "twitter:description")
            if parser.descriptions.get(key)
        ),
        None,
    )
    logo = next(
        (
            url
            for _, candidate in sorted(parser.icons, key=lambda item: item[0])
            if (url := image_url(candidate, parser.base))
        ),
        None,
    )
    preview = extract_image(result)
    return SourceProfile(
        description=description or None,
        website_url=result.final_url,
        logo_url=logo,
        image_url=preview.url if preview else None,
        language=parser.language,
    )

"""Deterministic presentation prerequisites, independent of editorial authority."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Protocol

_VERSION = re.compile(
    r"(?<!\w)(?:v\d+(?:\.\d+){0,3}|\d+(?:\.\d+){1,3})"
    r"(?:(?:a|b|rc)\d+|[-+][\w.-]+)?(?!\w)",
    re.IGNORECASE,
)
_RELEASE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "announcement",
        "announcing",
        "available",
        "changelog",
        "for",
        "here",
        "in",
        "is",
        "new",
        "notes",
        "now",
        "of",
        "out",
        "published",
        "release",
        "released",
        "releases",
        "s",
        "stable",
        "the",
        "version",
        "what",
    }
)


class _PresentationText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)


def presentation_text(value: str | None) -> str:
    parser = _PresentationText()
    parser.feed((value or "")[:100_000])
    text = " ".join(parser.parts)
    # Destinations alone do not make a readable description or identifying title.
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?:https?://|www\.)\S+", " ", text)
    return " ".join(text.split())


def readable_presentation(value: str | None) -> bool:
    return any(character.isalpha() for character in presentation_text(value))


def identifying_title(value: str | None) -> bool:
    """Reject empty titles and bare release labels without guessing a project."""
    title = _VERSION.sub(" ", presentation_text(value)).casefold()
    words = re.findall(r"[^\W\d_]+", title, flags=re.UNICODE)
    return any(word not in _RELEASE_WORDS for word in words)


class ArticlePresentation(Protocol):
    title: str
    ai_title: str | None
    ai_summary: str | None
    ai_description: str | None


def presentation_blockers(article: ArticlePresentation) -> list[str]:
    """Required fields are usable text; this does not certify semantic quality."""
    reasons = []
    if not identifying_title(article.ai_title or article.title):
        reasons.append("unidentifiable_title")
    if not readable_presentation(article.ai_summary):
        reasons.append("missing_ai_summary")
    if not readable_presentation(article.ai_description):
        reasons.append("missing_ai_description")
    return reasons

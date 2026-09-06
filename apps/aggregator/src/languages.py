"""Offline language inference from publisher text, never a source-wide language hint."""

import logging
import os
import re
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from devfeed_core.feeds.parser import ParsedFeed
from devfeed_core.logging import elapsed_ms
from devfeed_core.source_types import SourceType

if TYPE_CHECKING:
    from lingua import LanguageDetector

logger = logging.getLogger(__name__)
MIN_LETTERS = 40
SUMMARY_LETTERS = 100
MIN_CONFIDENCE = 0.80
MIN_MARGIN = 0.20
MAX_TEXT = 2500


@dataclass(frozen=True)
class LanguageDetection:
    language: str | None
    # Relative model score, not a calibrated probability of being correct.
    confidence: float | None
    reason: str
    text_source: str | None = None


class ProseExtractor(HTMLParser):
    """Ignore code and invisible markup; programming syntax is not natural language."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "pre", "code"}:
            self.suppressed.append(tag)
        if not self.suppressed:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.suppressed:
            self.suppressed = self.suppressed[: self.suppressed.index(tag)]
        if not self.suppressed:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def prose(value: str) -> str:
    value = value[:100_000]
    value = re.sub(r"(`{3,}|~{3,}).*?(?:\1|\Z)", " ", value, flags=re.S)
    value = re.sub(r"`[^`\n]*`", " ", value)
    parser = ProseExtractor()
    parser.feed(value)
    value = " ".join(parser.parts)
    # Keep Markdown link labels but remove destinations, URLs and email addresses.
    value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"(?:https?://|www\.)\S+|\S+@\S+", " ", value)
    return re.sub(r"\s+", " ", value).strip()[:MAX_TEXT]


def letter_count(value: str) -> int:
    return sum(character.isalpha() for character in value)


@lru_cache(maxsize=1)
def _detector(pid: int) -> "LanguageDetector":
    # Lazy, per-process initialization: no models loaded during API admission,
    # CLI help, or in the RQ parent before forking work horses. Models ship in the
    # package; there are no runtime downloads, external calls or language allowlists.
    from lingua import LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_all_languages().build()


def detect_language(title: str, summary: str, source_type: str | None) -> LanguageDetection:
    if source_type not in {SourceType.PUBLISHER, "page"}:
        return LanguageDetection(None, None, "publisher_text_required")
    clean_summary = prose(summary)
    if letter_count(clean_summary) >= SUMMARY_LETTERS:
        # Long-form prose wins over English tool names or translated headlines.
        text, basis = clean_summary, "summary"
    else:
        text, basis = prose(title) + " " + clean_summary, "title_summary"
    if letter_count(text) < MIN_LETTERS:
        return LanguageDetection(None, None, "insufficient_text", basis)
    scores = _detector(os.getpid()).compute_language_confidence_values(text[:MAX_TEXT])
    if not scores:
        return LanguageDetection(None, None, "uncertain", basis)
    best = scores[0]
    runner_up = scores[1].value if len(scores) > 1 else 0.0
    confidence = round(best.value, 6)
    if best.value < MIN_CONFIDENCE or best.value - runner_up < MIN_MARGIN:
        return LanguageDetection(None, confidence, "uncertain", basis)
    return LanguageDetection(
        best.language.iso_code_639_1.name.lower(), confidence, "detected", basis
    )


def detect_feed_languages(parsed: ParsedFeed) -> ParsedFeed:
    """Run within the ingestion job, before opening the persistence transaction."""
    started = time.perf_counter()
    entries = [
        replace(
            entry,
            language=detect_language(entry.title, entry.summary, entry.source_type).language,
        )
        for entry in parsed.entries
    ]
    detected = sum(entry.language is not None for entry in entries)
    logger.info(
        "article_languages_detected",
        extra={
            "languages_detected": detected,
            "languages_unknown": len(entries) - detected,
            "duration_ms": elapsed_ms(started),
        },
    )
    return replace(parsed, entries=entries)

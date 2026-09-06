import re
import uuid
from collections.abc import Iterable

from devfeed_core.models import Category, Tag


def has_term(content: str, terms: list[str]) -> bool:
    return any(
        re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", content)
        for term in terms
        if term.strip()
    )


def classify_tags(
    title: str, summary: str, publisher_tags: list[str], tags: Iterable[Tag]
) -> list[uuid.UUID]:
    content = " ".join([title, summary, *publisher_tags]).casefold()
    return [tag.id for tag in tags if has_term(content, [tag.name, tag.slug, *tag.aliases])]


def detect_content_type(title: str, publisher_tags: list[str]) -> str:
    content = " ".join([title, *publisher_tags]).casefold()
    for kind, terms in [
        ("release", ["release", "released", "release notes", "changelog"]),
        ("tutorial", ["tutorial", "how to", "step-by-step", "getting started", "guide"]),
        ("comparison", ["versus", "vs", "comparison"]),
        ("opinion", ["opinion", "my thoughts"]),
        ("news", ["news", "announces", "announced"]),
    ]:
        if has_term(content, terms):
            return kind
    return "article"


def classify(
    title: str, summary: str, tags: list[str], categories: Iterable[Category]
) -> list[uuid.UUID]:
    """Deterministic multi-label baseline; unknown topics remain uncategorized."""
    content = " ".join([title, summary, *tags]).casefold()
    return [category.id for category in categories if has_term(content, category.keywords)]

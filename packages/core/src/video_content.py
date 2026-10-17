"""Substantive video evidence uses provider metadata, never a scraped watch page."""

import re
from datetime import datetime

from devfeed_core.source_profiles import language_code

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def youtube_video(article) -> bool:
    return getattr(article, "provider", None) == "youtube"


def video_metadata(article) -> dict:
    value = getattr(article, "video_metadata", None)
    return value if isinstance(value, dict) else {}


def video_description(article, content=None) -> str:
    metadata = video_metadata(article)
    if content is not None and content.method == "youtube_metadata" and content.text:
        return content.text[:5000]
    value = metadata.get("description")
    return value[:5000] if isinstance(value, str) else ""


def verified_video_metadata(article) -> bool:
    """Require the adapter's stable identity, public metadata and retrieval evidence."""
    identifier = getattr(article, "provider_resource_id", None)
    metadata = video_metadata(article)
    retrieved = metadata.get("retrieved_at")
    try:
        moment = datetime.fromisoformat(retrieved) if isinstance(retrieved, str) else None
    except ValueError:
        moment = None
    return bool(
        youtube_video(article)
        and isinstance(identifier, str)
        and VIDEO_ID.fullmatch(identifier)
        and article.canonical_url == f"https://www.youtube.com/watch?v={identifier}"
        and isinstance(metadata.get("channel_id"), str)
        and CHANNEL_ID.fullmatch(metadata["channel_id"])
        and metadata.get("availability") == "public"
        and moment is not None
        and moment.tzinfo is not None
    )


def has_video_description(article, content=None) -> bool:
    description = " ".join(video_description(article, content).split())
    title = " ".join(str(article.title or "").split())
    return (
        any(character.isalpha() for character in description)
        and description.casefold() != title.casefold()
    )


def automatic_video_evidence(description: str, title: str) -> bool:
    """A conservative automation floor, never a relevance or rejection decision.

    Short descriptions still support explicit editorial review. Word count cannot
    establish relevance; classification must independently assess the supplied text.
    """
    description = " ".join(description.split())
    return (
        description.casefold() != " ".join(title.split()).casefold()
        and len(re.findall(r"\w+", description)) >= 20
    )


def video_language(article) -> str | None:
    value = language_code(video_metadata(article).get("language"))
    return value if value not in {"und", "mul", "zxx"} else None


def substantive_page_kind(article, page_kind: str | None) -> bool:
    if youtube_video(article):
        return page_kind == "video" and article.content_format == "video"
    return page_kind == "article"

"""Resolve publication identity from already-loaded source provenance."""

from urllib.parse import urlsplit

from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.source_types import SourceType
from devfeed_core.urls import validate_public_url


def publication_host(value: str | None) -> str | None:
    if not value:
        return None
    try:
        host = urlsplit(validate_public_url(value)).hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


def original_publisher(article: Article, origins: list[ArticleOrigin]) -> Source | None:
    """Require one approved publisher origin on the original article's website.

    A curator is never the publisher of a linked article. Feed hosts are a fallback
    only when no publication website is configured; a feed delivery service must
    not override a known publication website. Ambiguous identities stay unknown.
    """
    host = publication_host(article.canonical_url)
    if not host:
        return None
    candidates = {}
    for origin in origins:
        source = origin.source
        if (
            source.approval_status == "approved"
            and source.source_type == SourceType.PUBLISHER
            and source.name.strip()
            and publication_host(source.website_url or source.feed_url) == host
        ):
            candidates[source.id] = source
    return next(iter(candidates.values())) if len(candidates) == 1 else None


def original_headline(
    article: Article, origins: list[ArticleOrigin], publisher: Source | None
) -> str | None:
    """Do not promote a retained curator title just because other fields came from a page."""
    if not article.title.strip():
        return None
    if article.metadata_source_type == "publisher":
        return article.title
    if publisher and any(
        origin.source_id == publisher.id
        and (origin.source_metadata or {}).get("title") == article.title
        for origin in origins
    ):
        return article.title
    return None

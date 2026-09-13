"""Configurable content age boundary; topic and relationship research is independent."""

from datetime import UTC, datetime, time

from sqlalchemy import true

from devfeed_core.config import get_settings
from devfeed_core.models import Article


def content_cutoff() -> datetime | None:
    value = get_settings().ai_content_not_before
    return datetime.combine(value, time.min, tzinfo=UTC) if value is not None else None


def eligible_content(published_at: datetime | None) -> bool:
    cutoff = content_cutoff()
    if cutoff is None:
        return True
    if published_at is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return published_at >= cutoff


def eligible_article(article: Article) -> bool:
    return eligible_content(article.published_at)


def eligible_articles():
    cutoff = content_cutoff()
    return Article.published_at >= cutoff if cutoff is not None else true()

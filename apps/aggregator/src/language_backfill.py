"""Bounded maintenance pass using exactly the worker's language inference policy."""

import logging
import time
import uuid

from devfeed_core.db import session_factory
from devfeed_core.logging import elapsed_ms
from devfeed_core.models import Article, ArticleOrigin
from sqlalchemy import select, update

from devfeed_aggregator.languages import LanguageDetection, detect_language

logger = logging.getLogger(__name__)


def backfill_languages(
    *,
    limit: int = 100,
    after: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500")
    started = time.perf_counter()
    statement = (
        select(
            Article.id,
            Article.title,
            Article.summary,
            Article.metadata_source_type,
            Article.language,
        )
        .where(
            Article.classification_provenance["origin"].as_string().is_(None),
            Article.publication_status == "unpublished",
        )
        .order_by(Article.id)
    )
    if after is not None:
        statement = statement.where(Article.id > after)
    if source_id is not None:
        statement = statement.where(
            select(ArticleOrigin.article_id)
            .where(ArticleOrigin.article_id == Article.id, ArticleOrigin.source_id == source_id)
            .exists()
        )
    factory = session_factory()
    with factory() as session:
        rows = session.execute(statement.limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    # No database connection/transaction is held during model initialization/inference.
    detections = [
        # The page worker saw main-body text; the stored short preview is weaker
        # evidence. Never replace that result with an excerpt-only maintenance pass.
        LanguageDetection(row.language, None, "page_body_already_detected")
        if row.metadata_source_type == "page" and row.language
        else detect_language(row.title, row.summary, row.metadata_source_type)
        for row in rows
    ]
    changed = [
        (row, result)
        for row, result in zip(rows, detections, strict=True)
        if row.language != result.language
    ]
    updated = set()
    if changed and not dry_run:
        with factory.begin() as session:
            for row, result in changed:
                identifier = session.scalar(
                    update(Article)
                    .where(
                        Article.id == row.id,
                        Article.title == row.title,
                        Article.summary == row.summary,
                        Article.metadata_source_type.is_not_distinct_from(row.metadata_source_type),
                        Article.language.is_not_distinct_from(row.language),
                        Article.classification_provenance["origin"].as_string().is_(None),
                        Article.publication_status == "unpublished",
                    )
                    .values(language=result.language)
                    .returning(Article.id)
                )
                if identifier is not None:
                    updated.add(identifier)
        # AppSession invalidates GET cache only after this transaction commits.
        # Compare-and-set prevents overwriting newer text or language from another worker.
    detected = sum(result.language is not None for result in detections)
    logger.info(
        "article_languages_backfilled",
        extra={
            "languages_detected": detected,
            "languages_unknown": len(rows) - detected,
            "articles_updated": len(updated),
            "dry_run": dry_run,
            "duration_ms": elapsed_ms(started),
        },
    )
    return {
        "dry_run": dry_run,
        "scanned": len(rows),
        "detected": detected,
        "unknown": len(rows) - detected,
        "would_change": len(changed),
        "updated": len(updated),
        "concurrent_changes_skipped": len(changed) - len(updated) if not dry_run else 0,
        "next_after": str(rows[-1].id) if more else None,
        "items": [
            {
                "id": str(row.id),
                "previous_language": row.language,
                "language": result.language,
                "confidence": result.confidence,
                "reason": result.reason,
                "text_source": result.text_source,
                "updated": row.id in updated,
            }
            for row, result in zip(rows, detections, strict=True)
        ],
    }

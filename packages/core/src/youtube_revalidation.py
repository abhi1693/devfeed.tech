"""Availability refreshes for videos whose collections no longer poll."""

from datetime import timedelta

from sqlalchemy import func, or_, select

from devfeed_core.article_jobs import request_article_enrichment
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleEnrichmentJob, ArticleOrigin, Source, utcnow
from devfeed_core.services import OperationConflict


def schedule_youtube_revalidation(factory, *, now=None) -> int:
    settings = get_settings()
    if not settings.youtube_api_key:
        return 0
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=settings.youtube_revalidate_seconds)
    recent_attempt = (
        select(
            func.max(
                func.coalesce(ArticleEnrichmentJob.finished_at, ArticleEnrichmentJob.created_at)
            )
        )
        .where(ArticleEnrichmentJob.article_id == Article.id)
        .scalar_subquery()
    )
    active = select(ArticleEnrichmentJob.id).where(
        ArticleEnrichmentJob.article_id == Article.id,
        ArticleEnrichmentJob.status.in_(["queued", "running"]),
    )
    approved_origin = (
        select(ArticleOrigin.id)
        .join(Source)
        .where(
            ArticleOrigin.article_id == Article.id,
            ArticleOrigin.active.is_(True),
            Source.approval_status == "approved",
        )
    )
    retrieved = Article.video_metadata["retrieved_at"].astext
    count = 0
    with factory.begin() as session:
        articles = session.scalars(
            select(Article)
            .where(
                Article.provider == "youtube",
                approved_origin.exists(),
                ~active.exists(),
                or_(retrieved.is_(None), retrieved < cutoff.isoformat()),
                or_(recent_attempt.is_(None), recent_attempt < cutoff),
            )
            .order_by(retrieved.asc().nullsfirst(), Article.id)
            .limit(settings.youtube_revalidation_batch_size)
            .with_for_update(of=Article, skip_locked=True)
        ).all()
        for article in articles:
            # All enqueue paths lock the article; recheck after acquiring it.
            if (
                session.scalar(
                    select(ArticleEnrichmentJob.id).where(
                        ArticleEnrichmentJob.article_id == article.id,
                        ArticleEnrichmentJob.status.in_(["queued", "running"]),
                    )
                )
                is not None
            ):
                continue
            try:
                request_article_enrichment(session, article.id, automatic=False)
            except OperationConflict:
                continue  # An approval change raced the candidate scan.
            count += 1
    return count

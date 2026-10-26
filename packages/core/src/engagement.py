"""Keep recent interaction rows bounded while preserving lifetime open totals."""

from datetime import timedelta

from sqlalchemy import delete, select, text, tuple_

from devfeed_core.models import ArticleOpen, utcnow


def prune_article_opens(factory, batch: int = 1000) -> None:
    with factory.begin() as session:
        # Capture must be active before pruning can preserve each expired
        # fact in its durable export queue. Explicit deletions never set
        # this flag and continue to propagate ordinary tombstones.
        if not session.scalar(text("SELECT enabled FROM analytics_pipeline_state LIMIT 1")):
            return
        session.execute(text("SET LOCAL devfeed.analytics_retention_delete = 'on'"))
        expired = (
            select(ArticleOpen.article_id, ArticleOpen.viewer_key, ArticleOpen.opened_hour)
            .where(ArticleOpen.opened_hour < utcnow() - timedelta(days=30))
            .order_by(ArticleOpen.opened_hour)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        session.execute(
            delete(ArticleOpen).where(
                tuple_(ArticleOpen.article_id, ArticleOpen.viewer_key, ArticleOpen.opened_hour).in_(
                    expired
                )
            )
        )

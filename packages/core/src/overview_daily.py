"""Bounded daily rollups, refreshed before pruning anonymous open events."""

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.models import Article, ArticleOpen, OverviewDaily, UserAccount, utcnow


def daily_metrics(session, start, now):
    """Backfill counts from retained records; unavailable open history stays null."""
    first_open_day = (now - timedelta(days=30)).date() + timedelta(days=1)
    values: dict[date, dict[str, Any]] = {
        start.date() + timedelta(days=i): {
            "added": 0,
            "published": 0,
            "accounts": 0,
            "opens": None,
            "readers": None,
            "multi_article_readers": None,
            "content_types": {},
        }
        for i in range((now.date() - start.date()).days + 1)
    }
    for bucket_day, metrics in values.items():
        if bucket_day >= first_open_day:
            metrics.update(opens=0, readers=0, multi_article_readers=0)
    for model, timestamp, metric in [
        (Article, Article.discovered_at, "added"),
        (Article, Article.published_to_feed_at, "published"),
        (UserAccount, UserAccount.created_at, "accounts"),
        (ArticleOpen, ArticleOpen.opened_hour, "opens"),
    ]:
        day = func.date(func.timezone("UTC", timestamp))
        for bucket, count in session.execute(
            select(day, func.count())
            .select_from(model)
            .where(timestamp >= start, timestamp <= now)
            .group_by(day)
        ):
            if metric != "opens" or bucket >= first_open_day:
                values[bucket][metric] = count
    # Aggregate per viewer/day first: repeat clicks on one article are not breadth.
    open_day = func.date(func.timezone("UTC", ArticleOpen.opened_hour))
    viewers = (
        select(
            open_day.label("day"),
            func.count(func.distinct(ArticleOpen.article_id)).label("articles"),
        )
        .where(ArticleOpen.opened_hour >= start, ArticleOpen.opened_hour <= now)
        .group_by(open_day, ArticleOpen.viewer_key)
        .subquery()
    )
    for bucket, readers, multiple in session.execute(
        select(viewers.c.day, func.count(), func.count().filter(viewers.c.articles > 1)).group_by(
            viewers.c.day
        )
    ):
        if bucket >= first_open_day:
            values[bucket].update(readers=readers, multi_article_readers=multiple)
    day = func.date(func.timezone("UTC", Article.published_to_feed_at))
    for bucket, kind, count in session.execute(
        select(day, Article.content_type, func.count())
        .where(Article.published_to_feed_at >= start, Article.published_to_feed_at <= now)
        .group_by(day, Article.content_type)
    ):
        values[bucket]["content_types"][kind] = count
    elapsed = func.extract("epoch", Article.published_to_feed_at - Article.discovered_at)
    for bucket, median in session.execute(
        select(day, func.percentile_cont(0.5).within_group(elapsed))
        .where(
            Article.published_to_feed_at >= start, Article.published_to_feed_at <= now, elapsed >= 0
        )
        .group_by(day)
    ):
        values[bucket]["median_publication_seconds"] = median
    return values


def refresh_overview_daily(factory):
    now = utcnow()
    with factory.begin() as session:
        # One scheduler owns a refresh; another scheduler may continue its other work.
        if not session.scalar(select(func.pg_try_advisory_xact_lock(284091))):
            return
        latest = session.scalar(select(OverviewDaily).order_by(OverviewDaily.day.desc()).limit(1))
        if latest and latest.updated_at > now - timedelta(minutes=5):
            return
        day = (
            max(
                now.date() - timedelta(days=179),
                latest.day - timedelta(days=1 if "readers" in latest.metrics else 29),
            )
            if latest
            else now.date() - timedelta(days=179)
        )
        start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
        metrics = daily_metrics(session, start, now)
        retained = dict(
            session.execute(
                select(OverviewDaily.day, OverviewDaily.metrics).where(OverviewDaily.day >= day)
            ).all()
        )
        for bucket, value in metrics.items():
            if bucket in retained:
                # Preserve all anonymous aggregates after raw events have expired.
                for key in ("opens", "readers", "multi_article_readers"):
                    if value[key] is None:
                        value[key] = retained[bucket].get(key)
        statement = insert(OverviewDaily).values(
            [{"day": day, "metrics": value, "updated_at": now} for day, value in metrics.items()]
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[OverviewDaily.day],
                set_={"metrics": statement.excluded.metrics, "updated_at": now},
            )
        )


def read_daily_metrics(session, start, now):
    """Use closed-day rollups; compute only gaps and the live current day."""
    rows = {
        row.day: row.metrics
        for row in session.scalars(
            select(OverviewDaily).where(
                OverviewDaily.day >= start.date(), OverviewDaily.day < now.date()
            )
        )
    }
    expected = [
        start.date() + timedelta(days=i) for i in range((now.date() - start.date()).days + 1)
    ]
    missing = next(
        (
            day
            for day in expected
            if day not in rows
            or (day >= (now - timedelta(days=29)).date() and "readers" not in rows[day])
        ),
        now.date(),
    )
    live = daily_metrics(session, datetime.combine(missing, time.min, tzinfo=now.tzinfo), now)
    # Existing history wins over re-reading events which may have been pruned.
    return {
        day: {
            "readers": None,
            "multi_article_readers": None,
            **live.get(day, {}),
            **rows.get(day, {}),
        }
        for day in expected
    }

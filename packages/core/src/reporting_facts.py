"""Bounded, replayable report contributions and reusable UTC aggregates.

Writers only enqueue entity identities. The publisher atomically replaces an
entity's previous contribution and adds its delta to small shared buckets. Reads
never fall back to historical article bodies, job JSON or review evidence.
"""

import time as monotonic_time
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    case,
    delete,
    event,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.sql.elements import ColumnElement

from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTopic,
    IngestionJob,
    ReportingArticleFact,
    ReportingArticleSource,
    ReportingArticleTopic,
    ReportingDailyFact,
    ReportingDirty,
    ReportingFactState,
    ReportingJobFact,
    ResearchVerificationJob,
    TopicAnalysisJob,
    utcnow,
)

ZERO_ID = UUID(int=0)
TOTAL_DAY = date.min
KIND_MODELS: dict[str, Any] = {
    "article": Article,
    "article_analysis": ArticleAnalysisJob,
    "topic_analysis": TopicAnalysisJob,
    "research_verification": ResearchVerificationJob,
    "ingestion": IngestionJob,
}
METRICS = ("count", "automatic", "tokens", "reported", "unreported", "duration_ms")
LOCK_ID = 3_924_039


class ReportingFactsUnavailable(RuntimeError):
    pass


def reporting_fact_lag(session, *, now=None):
    oldest = session.scalar(select(func.min(ReportingDirty.created_at)))
    return max(0.0, ((now or utcnow()) - oldest).total_seconds()) if oldest else 0.0


def require_reporting_facts(session):
    complete = session.scalar(
        select(func.count()).select_from(ReportingFactState).where(ReportingFactState.complete)
    )
    if complete != len(KIND_MODELS):
        raise ReportingFactsUnavailable("Reporting facts are still rebuilding")
    if reporting_fact_lag(session) > 60:
        raise ReportingFactsUnavailable("Reporting facts are catching up")


def reporting_fact_observation(session):
    """A committed observation watermark; pending recent changes remain explicit."""
    updated = session.scalar(select(func.min(ReportingFactState.updated_at)))
    return {
        "observed_at": updated.isoformat() if updated else None,
        "lag_seconds": reporting_fact_lag(session),
        "generation": session.scalar(select(func.max(ReportingFactState.generation))) or 0,
    }


def _add(deltas, day, kind, dimension, sign, **values):
    bucket = deltas[(day, kind, dimension)]
    for metric, value in values.items():
        bucket[metric] += Decimal(value) * sign


def _job_contribution(deltas, fact, sign):
    if fact is None:
        return
    if fact.kind == "ingestion":
        _add(
            deltas,
            fact.finished_at.astimezone(UTC).date(),
            "source.failed",
            fact.source_id,
            sign,
            count=1,
        )
    else:
        _add(
            deltas,
            fact.finished_at.astimezone(UTC).date(),
            "job." + fact.kind,
            ZERO_ID,
            sign,
            count=1,
            tokens=fact.tokens or 0,
            reported=int(fact.tokens is not None),
            unreported=int(fact.tokens is None),
            duration_ms=fact.duration_ms,
        )


def _article_contribution(deltas, fact, sources, topics, sign):
    if fact is None:
        return
    discovered = fact.discovered_at.astimezone(UTC).date()
    _add(deltas, discovered, "article.discovered", ZERO_ID, sign, count=1)
    for source_id in sources:
        _add(deltas, discovered, "source.discovered", source_id, sign, count=1)
    # Current inventory includes legacy publications without a first-publication
    # timestamp, and future-dated publications; neither belongs to a dated bucket.
    if fact.published:
        for topic_id in topics:
            _add(deltas, TOTAL_DAY, "topic.total", topic_id, sign, count=1)
    if fact.published_at is None:
        return
    published = fact.published_at.astimezone(UTC).date()
    _add(
        deltas,
        published,
        "article.published",
        ZERO_ID,
        sign,
        count=1,
        automatic=int(fact.automatic),
    )
    if fact.published:
        for source_id in sources:
            _add(deltas, published, "source.published", source_id, sign, count=1)
        for topic_id in topics:
            _add(deltas, published, "topic.published", topic_id, sign, count=1)


def _replace_jobs(session, kind, identifiers, deltas):
    model = KIND_MODELS[kind]
    old = {
        fact.entity_id: fact
        for fact in session.scalars(
            select(ReportingJobFact).where(
                ReportingJobFact.kind == kind, ReportingJobFact.entity_id.in_(identifiers)
            )
        )
    }
    columns = [model.id, model.finished_at]
    if kind == "ingestion":
        columns.extend(
            [model.source_id, literal(None).label("raw_tokens"), literal(0).label("duration_ms")]
        )
        condition = model.status == "failed"
    else:
        columns.extend(
            [
                literal(None).label("source_id"),
                func.left(model.usage["totalTokens"].astext, 19).label("raw_tokens"),
                model.duration_ms,
            ]
        )
        condition = model.status.in_(("succeeded", "failed"))
    rows = session.execute(
        select(*columns).where(model.id.in_(identifiers), condition, model.finished_at.is_not(None))
    ).mappings()
    current = {}
    for row in rows:
        raw = row["raw_tokens"]
        tokens = (
            Decimal(raw) if raw and raw.isascii() and raw.isdigit() and len(raw) <= 18 else None
        )
        current[row["id"]] = ReportingJobFact(
            kind=kind,
            entity_id=row["id"],
            finished_at=row["finished_at"],
            tokens=tokens,
            duration_ms=row["duration_ms"],
            source_id=row["source_id"],
        )
    fields = ("finished_at", "tokens", "duration_ms", "source_id")
    changed = [
        identifier
        for identifier in identifiers
        if tuple(getattr(old.get(identifier), key, None) for key in fields)
        != tuple(getattr(current.get(identifier), key, None) for key in fields)
    ]
    for identifier in changed:
        _job_contribution(deltas, old.get(identifier), -1)
        _job_contribution(deltas, current.get(identifier), 1)
    if changed:
        session.execute(
            delete(ReportingJobFact).where(
                ReportingJobFact.kind == kind, ReportingJobFact.entity_id.in_(changed)
            )
        )
        session.flush()
        session.add_all(current[identifier] for identifier in changed if identifier in current)


def _memberships(session, model, column, identifiers):
    output = defaultdict(set)
    for article_id, dimension in session.execute(
        select(model.article_id, column).where(model.article_id.in_(identifiers))
    ):
        output[article_id].add(dimension)
    return output


def _replace_articles(session, identifiers, deltas):
    old = {
        fact.article_id: fact
        for fact in session.scalars(
            select(ReportingArticleFact).where(ReportingArticleFact.article_id.in_(identifiers))
        )
    }
    old_sources = _memberships(
        session, ReportingArticleSource, ReportingArticleSource.source_id, identifiers
    )
    old_topics = _memberships(
        session, ReportingArticleTopic, ReportingArticleTopic.topic_id, identifiers
    )
    sources = _memberships(session, ArticleOrigin, ArticleOrigin.source_id, identifiers)
    topics = defaultdict(set)
    for article_id, topic_id in session.execute(
        select(ArticleTopic.article_id, ArticleTopic.topic_id).where(
            ArticleTopic.article_id.in_(identifiers),
            ArticleTopic.role.in_(("primary", "supporting")),
        )
    ):
        topics[article_id].add(topic_id)
    cutoff = (
        ArticleReview.article_id == Article.id,
        ArticleReview.created_at <= Article.published_to_feed_at,
    )
    automatic = (
        select(ArticleReview.id)
        .where(*cutoff, ArticleReview.action == "publish", ArticleReview.automation != {})
        .exists()
    )
    manual = select(ArticleReview.id).where(*cutoff, ArticleReview.automation == {}).exists()
    current = {}
    for row in session.execute(
        select(
            Article.id,
            Article.discovered_at,
            Article.published_to_feed_at,
            Article.publication_status,
            Article.content_type,
            (automatic & ~manual).label("automatic"),
        ).where(Article.id.in_(identifiers))
    ).mappings():
        published = row["published_to_feed_at"]
        elapsed = (
            (published.astimezone(UTC) - row["discovered_at"].astimezone(UTC))
            if published
            else None
        )
        latency = (
            (
                Decimal(elapsed.days * 86400 + elapsed.seconds)
                + Decimal(elapsed.microseconds) / 1_000_000
            )
            if elapsed is not None
            else None
        )
        current[row["id"]] = ReportingArticleFact(
            article_id=row["id"],
            content_type=row["content_type"],
            discovered_at=row["discovered_at"],
            published_at=published,
            published=row["publication_status"] == "published",
            automatic=row["automatic"],
            latency_seconds=latency if latency is not None and latency >= 0 else None,
        )
    fields = (
        "discovered_at",
        "published_at",
        "published",
        "automatic",
        "latency_seconds",
        "content_type",
    )
    changed_facts = [
        identifier
        for identifier in identifiers
        if tuple(getattr(old.get(identifier), key, None) for key in fields)
        != tuple(getattr(current.get(identifier), key, None) for key in fields)
    ]
    changed = [
        identifier
        for identifier in identifiers
        if identifier in changed_facts
        or old_sources[identifier] != sources[identifier]
        or old_topics[identifier] != topics[identifier]
    ]
    for identifier in changed:
        _article_contribution(
            deltas, old.get(identifier), old_sources[identifier], old_topics[identifier], -1
        )
        _article_contribution(
            deltas, current.get(identifier), sources[identifier], topics[identifier], 1
        )
    if changed_facts:
        session.execute(
            delete(ReportingArticleFact).where(ReportingArticleFact.article_id.in_(changed_facts))
        )
        session.flush()
        session.add_all(
            current[identifier] for identifier in changed_facts if identifier in current
        )
    for model, column, previous, observed in (
        (ReportingArticleSource, ReportingArticleSource.source_id, old_sources, sources),
        (ReportingArticleTopic, ReportingArticleTopic.topic_id, old_topics, topics),
    ):
        removed = [
            (identifier, dimension)
            for identifier in changed
            for dimension in previous[identifier] - observed[identifier]
        ]
        if removed:
            session.execute(delete(model).where(tuple_(model.article_id, column).in_(removed)))
        session.add_all(
            model(**{"article_id": identifier, column.key: dimension})
            for identifier in changed
            if identifier in current
            for dimension in observed[identifier] - previous[identifier]
        )


def _apply_deltas(session, deltas):
    rows = [
        dict(
            day=day, kind=kind, dimension_id=dimension, **{name: metrics[name] for name in METRICS}
        )
        for (day, kind, dimension), metrics in sorted(deltas.items())
        if any(metrics.values())
    ]
    for offset in range(0, len(rows), 200):
        batch = rows[offset : offset + 200]
        statement = insert(ReportingDailyFact).values(batch)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    ReportingDailyFact.day,
                    ReportingDailyFact.kind,
                    ReportingDailyFact.dimension_id,
                ],
                set_={
                    name: getattr(ReportingDailyFact, name) + getattr(statement.excluded, name)
                    for name in METRICS
                },
            )
        )
        session.execute(
            delete(ReportingDailyFact).where(
                tuple_(
                    ReportingDailyFact.day, ReportingDailyFact.kind, ReportingDailyFact.dimension_id
                ).in_([(row["day"], row["kind"], row["dimension_id"]) for row in batch]),
                *(getattr(ReportingDailyFact, name) == 0 for name in METRICS),
            )
        )


@contextmanager
def batch_budget(session, seconds=30):
    """Bound the whole transaction and its last statement, including wait time."""
    connection = session.connection()
    deadline = monotonic_time.monotonic() + seconds

    def bound(_connection, cursor, _statement, _parameters, _context, _many):
        remaining = int((deadline - monotonic_time.monotonic()) * 1000)
        if remaining <= 0:
            raise DatabaseTimeout("Reporting fact batch exceeded its time budget")
        cursor.execute(f"SET LOCAL statement_timeout = '{min(10000, remaining)}ms'")

    event.listen(connection, "before_cursor_execute", bound)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", bound)


def refresh_reporting_facts(factory, *, limit=100):
    """Atomically project at most ``limit`` entity contributions in one transaction.

    Initial coverage progresses one source kind at a time. Every ordinary update
    remains durably dirty during that scan, so UUIDs inserted behind its cursor and
    deletes/edits racing its pages are reconciled before readiness can become fresh.
    A single advisory transaction lock serializes bootstrap, replay and bucket deltas.
    """
    if not 1 <= limit <= 200:
        raise ValueError("Reporting fact batches must contain 1..200 entities")
    with factory() as session, session.begin(), batch_budget(session):
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        if not session.scalar(select(func.pg_try_advisory_xact_lock(LOCK_ID))):
            return 0
        for kind in KIND_MODELS:
            session.execute(insert(ReportingFactState).values(kind=kind).on_conflict_do_nothing())
        state = session.scalar(
            select(ReportingFactState)
            .where(~ReportingFactState.complete)
            .order_by(ReportingFactState.kind)
            .limit(1)
            .with_for_update()
        )
        groups = {}
        dirty = []
        if state is not None:
            model = KIND_MODELS[state.kind]
            query = select(model.id).order_by(model.id).limit(limit)
            if state.cursor_id is not None:
                query = query.where(model.id > state.cursor_id)
            identifiers = list(session.scalars(query))
            groups[state.kind] = identifiers
            dirty = list(
                session.scalars(
                    select(ReportingDirty).where(
                        ReportingDirty.kind == state.kind, ReportingDirty.entity_id.in_(identifiers)
                    )
                )
            )
            if identifiers:
                state.cursor_id = identifiers[-1]
            state.complete = len(identifiers) < limit
            state.updated_at = utcnow()
        else:
            dirty = list(
                session.scalars(
                    select(ReportingDirty)
                    .order_by(
                        ReportingDirty.created_at, ReportingDirty.kind, ReportingDirty.entity_id
                    )
                    .limit(limit)
                )
            )
            for row in dirty:
                groups.setdefault(row.kind, []).append(row.entity_id)
        deltas: defaultdict[tuple[date, str, UUID], defaultdict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        for kind, identifiers in groups.items():
            if not identifiers:
                continue
            if kind == "article":
                _replace_articles(session, identifiers, deltas)
            else:
                _replace_jobs(session, kind, identifiers, deltas)
        _apply_deltas(session, deltas)
        for row in dirty:
            # A writer that changes this identity during projection advances the
            # generation; the conditional acknowledgement cannot lose that work.
            session.execute(
                delete(ReportingDirty).where(
                    ReportingDirty.kind == row.kind,
                    ReportingDirty.entity_id == row.entity_id,
                    ReportingDirty.generation == row.generation,
                )
            )
        if state is not None or dirty:
            session.execute(
                update(ReportingFactState).values(
                    updated_at=utcnow(), generation=ReportingFactState.generation + 1
                )
            )
        return sum(len(identifiers) for identifiers in groups.values())


def _edge_rows(kind):
    """Compact contributions used only for incomplete UTC days at range edges."""
    dimension: ColumnElement = literal(ZERO_ID).label("dimension_id")
    count = literal(1).label("count")
    zero = literal(0)
    if kind.startswith("job.") or kind == "source.failed":
        fact = ReportingJobFact
        condition = fact.kind == (kind[4:] if kind.startswith("job.") else "ingestion")
        if kind == "source.failed":
            dimension = fact.source_id.label("dimension_id")
        return (
            select(
                fact.finished_at.label("occurred_at"),
                dimension,
                count,
                zero.label("automatic"),
                func.coalesce(fact.tokens, 0).label("tokens"),
                case((fact.tokens.is_not(None), 1), else_=0).label("reported"),
                case((fact.tokens.is_(None), 1), else_=0).label("unreported"),
                fact.duration_ms,
            )
            .where(condition)
            .subquery()
        )
    article_fact = ReportingArticleFact
    occurred = (
        article_fact.discovered_at if kind.endswith("discovered") else article_fact.published_at
    )
    query = select(
        occurred.label("occurred_at"),
        dimension,
        count,
        case((article_fact.automatic, 1), else_=0).label("automatic"),
        zero.label("tokens"),
        zero.label("reported"),
        zero.label("unreported"),
        zero.label("duration_ms"),
    )
    if kind.startswith("source."):
        query = query.join(
            ReportingArticleSource, ReportingArticleSource.article_id == article_fact.article_id
        )
        query = query.with_only_columns(
            occurred.label("occurred_at"),
            ReportingArticleSource.source_id.label("dimension_id"),
            count,
            *(zero.label(name) for name in METRICS if name != "count"),
        )
    elif kind == "topic.published":
        query = query.join(
            ReportingArticleTopic, ReportingArticleTopic.article_id == article_fact.article_id
        )
        query = query.with_only_columns(
            occurred.label("occurred_at"),
            ReportingArticleTopic.topic_id.label("dimension_id"),
            count,
            *(zero.label(name) for name in METRICS if name != "count"),
        )
    if kind in ("source.published", "topic.published"):
        query = query.where(article_fact.published)
    return query.subquery()


def bucket_query(kind, start, now):
    """Inclusive timestamps, UTC buckets, and exact partial-day correction."""
    start, now = start.astimezone(UTC), now.astimezone(UTC)
    first = start.date() + timedelta(days=int(start.timetz().replace(tzinfo=None) != time.min))
    full_start = datetime.combine(first, time.min, tzinfo=UTC)
    today = datetime.combine(now.date(), time.min, tzinfo=UTC)
    daily = select(
        ReportingDailyFact.day,
        ReportingDailyFact.dimension_id,
        *(getattr(ReportingDailyFact, name) for name in METRICS),
    ).where(
        ReportingDailyFact.kind == kind,
        ReportingDailyFact.day >= first,
        ReportingDailyFact.day < now.date(),
        ReportingDailyFact.day != TOTAL_DAY,
    )
    edges = _edge_rows(kind)
    day = func.date(func.timezone("UTC", edges.c.occurred_at))
    partial = (
        select(
            day.label("day"),
            edges.c.dimension_id,
            *(func.sum(edges.c[name]).label(name) for name in METRICS),
        )
        .where(
            edges.c.occurred_at >= start,
            edges.c.occurred_at <= now,
            or_(edges.c.occurred_at < full_start, edges.c.occurred_at >= today),
        )
        .group_by(day, edges.c.dimension_id)
    )
    return union_all(daily, partial).subquery()


def publication_latency(session, start, previous_start, now):
    require_reporting_facts(session)
    fact = ReportingArticleFact
    return session.execute(
        select(
            func.percentile_cont(0.5)
            .within_group(fact.latency_seconds)
            .filter(fact.published_at >= start),
            func.percentile_cont(0.5)
            .within_group(fact.latency_seconds)
            .filter(fact.published_at < start),
            func.percentile_cont(0.9)
            .within_group(fact.latency_seconds)
            .filter(fact.published_at >= start),
        ).where(
            fact.published_at >= previous_start,
            fact.published_at <= now,
            fact.latency_seconds.is_not(None),
        )
    ).one()

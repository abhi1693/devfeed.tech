"""Indexed, read-only pipeline serving. Calculation belongs to the projector."""

from datetime import datetime, timedelta
from typing import Literal

from devfeed_core.models import PipelineProjection, ReadModelEvent, ReadModelState, utcnow
from devfeed_core.read_models import ReadModelSnapshot, pipeline_policy_version
from devfeed_core.text_search import normalized_text, text_search
from fastapi import HTTPException
from sqlalchemy import case, func, literal, or_, select, text
from sqlalchemy.engine import RowMapping

PUBLISHER_FRESH_SECONDS = 60


def serving_projection(session, kind: Literal["article", "source"] | None = None):
    if not session.in_transaction():
        session.connection(
            execution_options={
                "isolation_level": "REPEATABLE READ",
                "postgresql_readonly": True,
            }
        )
        session.execute(text("SET LOCAL statement_timeout = '2s'"))
    state = session.get(ReadModelState, "pipeline")
    policy = state.payload.get("serving_policy") if state else None
    if not policy:
        raise HTTPException(
            503,
            {"code": "projection_initializing", "message": "Pipeline views are being prepared."},
            headers={"Retry-After": "2"},
        )
    # Catalog coverage is incremental and can take longer than the operational
    # freshness budget. Keep its age separate from unapplied entity changes.
    pending = select(func.min(ReadModelEvent.created_at)).where(ReadModelEvent.kind != "catalog")
    if kind == "article":
        pending = pending.where(
            ReadModelEvent.kind.in_(("article", "backfill_article", "source_fanout", "policy"))
        )
    elif kind == "source":
        pending = pending.where(
            ReadModelEvent.kind.in_(
                ("source", "candidate", "backfill_source", "backfill_candidate", "policy")
            )
        )
    if kind == "source":
        oldest, catalog_oldest = session.scalar(pending), None
    else:
        catalog_pending = select(func.min(ReadModelEvent.created_at)).where(
            ReadModelEvent.kind == "catalog"
        )
        # Scalar subqueries retain PostgreSQL's indexed MIN probes without a
        # full-backlog aggregate or an additional interactive round trip.
        oldest, catalog_oldest = session.execute(
            select(pending.scalar_subquery(), catalog_pending.scalar_subquery())
        ).one()
    now = utcnow()
    transitions = select(func.min(PipelineProjection.next_transition_at)).where(
        PipelineProjection.policy_version == policy,
        PipelineProjection.next_transition_at <= now,
    )
    if kind is not None:
        transitions = transitions.where(
            PipelineProjection.kind.in_(
                ("source", "candidate") if kind == "source" else ("article",)
            )
        )
    due = session.scalar(transitions)
    if due is not None:
        oldest = min(oldest, due) if oldest else due
    refresh = state.payload.get("catalog_refresh") if kind != "source" else None
    if refresh:
        started = datetime.fromisoformat(refresh["started_at"])
        catalog_oldest = min(catalog_oldest, started) if catalog_oldest else started
    publisher_age = max(0.0, (now - state.updated_at).total_seconds())
    lag = max(publisher_age, max(0.0, (now - oldest).total_seconds()) if oldest else 0.0)
    if refresh:
        progress = datetime.fromisoformat(refresh.get("progress_at", refresh["started_at"]))
        lag = max(lag, max(0.0, (now - progress).total_seconds()))
    elif catalog_oldest is not None:
        # A queued scan must also start promptly; an active publisher heartbeat
        # alone cannot disguise a catalog event that is never being expanded.
        # A just-completed pass may leave earlier edits queued for its successor;
        # measure that handoff from real progress, preserving the full demand age
        # separately as catalog lag.
        progress = state.payload.get("catalog_progress_at")
        waiting_since = (
            max(catalog_oldest, datetime.fromisoformat(progress)) if progress else catalog_oldest
        )
        lag = max(lag, max(0.0, (now - waiting_since).total_seconds()))
    rebuilding = (
        state.payload.get("target_policy") != policy
        or pipeline_policy_version() != policy
        or state.payload.get("backfill_complete") is False
    )
    generated = (
        state.payload.get(f"{kind}_generated_at") if kind is not None else None
    ) or state.payload.get("serving_generated_at")
    snapshot = ReadModelSnapshot(
        status="rebuilding"
        if rebuilding
        else "stale"
        if lag > PUBLISHER_FRESH_SECONDS
        else "catching_up"
        if catalog_oldest
        else "refreshing"
        if oldest
        else "fresh",
        generated_at=datetime.fromisoformat(generated) if generated else state.updated_at,
        generation=policy
        if kind == "source"
        else f"{policy}:{state.payload.get('article_generation', 0)}",
        policy_version=policy,
        article_generation=state.payload.get("article_generation", 0) if kind != "source" else None,
        catalog_generation=state.payload.get("catalog_generation", 0) if kind != "source" else None,
        catalog_revision=state.payload.get("catalog_revision") if kind != "source" else None,
        catalog_target_revision=(
            state.payload.get(
                "catalog_target_revision", refresh.get("revision") if refresh else None
            )
            if kind != "source"
            else None
        ),
        catalog_lag_seconds=(
            max(0.0, (now - catalog_oldest).total_seconds()) if catalog_oldest else None
        ),
        lag_seconds=lag,
    )
    return policy, snapshot


def _article_source_groups(session, statement, stages, limit, offset):
    """Aggregate once and page every stage with independent window ranks."""
    row = PipelineProjection
    states = (
        statement.where(row.stage.in_(stages))
        .with_only_columns(
            row.stage.label("stage"),
            row.source_id.label("source_id"),
            row.state.label("state"),
            func.min(row.source_name).label("source_name"),
            func.min(row.payload["_source_sort"].astext).label("source_sort"),
            func.count().label("n"),
        )
        .group_by(row.stage, row.source_id, row.state)
        .subquery()
    )
    grouped = (
        select(
            states.c.stage,
            states.c.source_id,
            func.min(states.c.source_name).label("source_name"),
            func.sum(states.c.n).label("total"),
            func.jsonb_object_agg(states.c.state, states.c.n).label("state_counts"),
            func.min(
                case(
                    (states.c.state == "running", 0),
                    (states.c.state.in_(("blocked", "retry_wait")), 1),
                    else_=2,
                )
            ).label("priority"),
            func.min(states.c.source_sort).label("source_sort"),
        )
        .group_by(states.c.stage, states.c.source_id)
        .subquery()
    )
    ranked = select(
        grouped,
        func.count().over(partition_by=grouped.c.stage).label("group_count"),
        func.row_number()
        .over(
            partition_by=grouped.c.stage,
            order_by=(
                grouped.c.priority,
                grouped.c.source_name.is_(None),
                grouped.c.source_sort.collate("C"),
                grouped.c.source_id,
            ),
        )
        .label("position"),
    ).subquery()
    result: dict[str, list[RowMapping]] = {key: [] for key in stages}
    for group in session.execute(
        select(ranked)
        .where(ranked.c.position > offset, ranked.c.position <= offset + limit)
        .order_by(ranked.c.stage, ranked.c.position)
    ).mappings():
        result[group["stage"]].append(group)
    return result


def article_board(
    session,
    *,
    q,
    source_id,
    needs_attention,
    limit,
    stage=None,
    cursor=0,
    group_by=None,
    source_group=None,
):
    from devfeed_admin_api.article_pipeline import (
        STAGES,
        PipelineBoard,
        PipelineCard,
        PipelineColumn,
        PipelineSourceGroup,
    )

    policy, snapshot = serving_projection(session, kind="article")
    now = utcnow()
    since = now - timedelta(days=7)
    row = PipelineProjection
    statement = select(row).where(row.kind == "article", row.policy_version == policy)
    statement = statement.where(or_(row.terminal_at.is_(None), row.terminal_at >= since))
    if q:
        needle = normalized_text(literal(q))
        legacy_search = text_search(
            q,
            row.payload["_search"]["title"].astext,
            row.payload["_search"]["ai_title"].astext,
            row.payload["_search"]["canonical_url"].astext,
        )
        statement = statement.where(
            case(
                (needle == "", legacy_search),
                else_=func.strpos(row.search_text, needle) > 0,
            )
            if policy == pipeline_policy_version()
            else legacy_search
        )
    if source_id:
        statement = statement.where(row.payload["_source_ids"].contains([str(source_id)]))
    if source_group is not None:
        statement = statement.where(
            row.source_id.is_(None) if source_group == "unknown" else row.source_id == source_group
        )
    if needs_attention:
        statement = statement.where(row.state.in_(("blocked", "retry_wait")))
    stages = (stage,) if stage else STAGES
    counts = dict(
        session.execute(
            statement.with_only_columns(row.stage, func.count()).group_by(row.stage)
        ).all()
    )
    grouped_stages = (
        _article_source_groups(session, statement, stages, limit, cursor if stage else 0)
        if group_by == "source"
        else {}
    )
    columns = []
    for key in stages:
        offset = cursor if stage else 0
        filtered = statement.where(row.stage == key)
        total = counts.get(key, 0)
        if group_by == "source":
            groups = grouped_stages[key]
            n_groups = groups[0]["group_count"] if groups else 0
            columns.append(
                PipelineColumn(
                    stage=key,
                    total=total,
                    items=[],
                    source_groups=[
                        PipelineSourceGroup(
                            **{
                                name: group[name]
                                for name in ("source_id", "source_name", "total", "state_counts")
                            }
                        )
                        for group in groups
                    ],
                    next_cursor=offset + limit if offset + limit < n_groups else None,
                )
            )
        else:
            ordering = (
                (row.terminal_at.desc(), row.entity_id.desc())
                if key in {"published", "rejected"}
                else (case((row.state == "running", 0), else_=1), row.created_at, row.entity_id)
            )
            payloads = session.scalars(
                filtered.with_only_columns(row.payload)
                .order_by(*ordering)
                .offset(offset)
                .limit(limit)
            )
            columns.append(
                PipelineColumn(
                    stage=key,
                    total=total,
                    items=[PipelineCard.model_validate(payload) for payload in payloads],
                    next_cursor=offset + limit if offset + limit < total else None,
                )
            )
    return PipelineBoard(
        as_of=now,
        terminal_since=since,
        counts={column.stage: column.total for column in columns},
        columns=columns,
        snapshot=snapshot,
    )


def source_columns(session, query, now, *, stage=None, cursor=0, policy=None):
    from devfeed_admin_api.source_pipeline import (
        STAGES,
        TERMINAL_STAGES,
        SourcePipelineCard,
        SourcePipelineColumn,
    )

    if policy is None:
        policy, _ = serving_projection(session, kind="source")
    row = PipelineProjection
    statement = select(row).where(
        row.kind.in_(("source", "candidate")), row.policy_version == policy
    )
    statement = statement.where(
        or_(
            row.stage.not_in(TERMINAL_STAGES),
            row.has_active_work,
            row.activity_at >= query.terminal_since,
        )
    )
    if query.q:
        statement = statement.where(
            text_search(
                query.q,
                row.payload["name"].astext,
                row.payload["website_url"].astext,
                row.payload["feed_url"].astext,
            )
        )
    if query.source_type:
        statement = statement.where(row.payload["source_type"].astext == query.source_type)
    if query.needs_attention:
        statement = statement.where(
            or_(row.state.in_(("blocked", "retry_wait")), row.stage == "review")
        )
    counts = dict(
        session.execute(
            statement.with_only_columns(row.stage, func.count()).group_by(row.stage)
        ).all()
    )
    columns = []
    for key in (stage,) if stage else STAGES:
        total = counts.get(key, 0)
        ordering = (
            case((row.state == "running", 0), else_=1),
            row.activity_at.desc() if key in TERMINAL_STAGES else row.created_at,
            row.kind,
            row.entity_id,
        )
        payloads = session.scalars(
            statement.where(row.stage == key)
            .with_only_columns(row.payload)
            .order_by(*ordering)
            .offset(cursor)
            .limit(query.limit)
        )
        items = [SourcePipelineCard.model_validate(payload) for payload in payloads]
        columns.append(
            SourcePipelineColumn(
                stage=key,
                total=total,
                items=items,
                next_cursor=cursor + len(items) if cursor + len(items) < total else None,
            )
        )
    return columns

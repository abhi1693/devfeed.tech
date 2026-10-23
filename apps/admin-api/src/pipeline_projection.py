"""Durable incremental pipeline projection, independent of interactive requests.

Captured event IDs are acknowledged with the projection write. A bounded,
token-guarded lease serializes publishers without pinning a database connection
while Python evaluates article evidence. Policy rebuilds publish alongside the
last complete serving policy and switch only when their backfill finishes.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from devfeed_core.analysis import catalog, reuse_candidates
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.catalog_cache import reuse_snapshots
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleOrigin,
    ArticleReview,
    CatalogRevision,
    PipelineProjection,
    ReadModelEvent,
    ReadModelState,
    Source,
    SourceCandidate,
    utcnow,
)
from devfeed_core.read_models import (
    EventKind,
    acknowledge_events,
    enqueue_event,
    event_batch,
    pipeline_policy_version,
    set_state,
)
from devfeed_core.source_lifecycle import source_readiness_batch
from devfeed_core.text_search import normalized_text
from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

LEASE_SECONDS = 120


def _next_transition(jobs, now):
    return min(
        (
            job.available_at
            for job in jobs
            if job is not None
            and getattr(job, "status", None) == "queued"
            and job.available_at > now
        ),
        default=None,
    )


def article_rows(factory, identifiers, policy, now):
    from devfeed_admin_api.article_pipeline import _batch_facts, _card, _statement
    from devfeed_admin_api.automation import automation_blocker_rows

    with factory() as session:
        session.execute(select(func.set_config("statement_timeout", "10000", True)))
        loaded = session.execute(
            _statement(None, None)
            .where(Article.id.in_(identifiers))
            .add_columns(
                func.concat_ws(
                    "\n",
                    *(
                        normalized_text(getattr(Article, name))
                        for name in ("title", "ai_title", "canonical_url")
                    ),
                )
            )
        ).all()
        articles = [article for article, _ in loaded]
        searches = {article.id: value for article, value in loaded}
        rejected = dict(
            session.execute(
                select(
                    ArticleReview.article_id,
                    func.max(ArticleReview.created_at),
                )
                .where(ArticleReview.article_id.in_(identifiers), ArticleReview.action == "reject")
                .group_by(ArticleReview.article_id)
            ).all()
        )
        terminal_times = {
            item.id: item.published_to_feed_at
            if item.publication_status == "published"
            else rejected.get(item.id, item.discovered_at)
            if item.review_status == "rejected"
            else None
            for item in articles
        }
        articles = [
            item
            for item in articles
            if (terminal_times[item.id] is None and item.publication_status != "published")
            or (
                terminal_times[item.id] is not None
                and terminal_times[item.id] >= now - timedelta(days=7)
            )
        ]
        if not articles:
            return []
        taxonomy = catalog(session)
        facts = _batch_facts(session, articles, lightweight=True)
        ids = [item.id for item in articles]
        list(session.scalars(select(ArticleContent).where(ArticleContent.article_id.in_(ids))))
        analysis_ids = [job.id for job in facts["analysis"].values()]
        if analysis_ids:
            list(
                session.scalars(
                    select(ArticleAnalysisJob)
                    .options(load_only(ArticleAnalysisJob.id, ArticleAnalysisJob.catalog_snapshot))
                    .where(ArticleAnalysisJob.id.in_(analysis_ids))
                )
            )
        blockers = automation_blocker_rows(session, ids)
        session.expunge_all()
    # No connection is held during catalog ranking, body hashing, and card evaluation.
    rows = []
    with reuse_candidates(reuse_existing=True):
        for article in articles:
            current = {key: values.get(article.id) for key, values in facts.items()}
            if article.review_status == "rejected":
                current = dict.fromkeys(current)
            readiness = derive_publication_readiness(
                article,
                **current,
                taxonomy=taxonomy,
                settings=get_settings(),
                now=now,
            )
            card = _card(article, readiness, current, taxonomy=taxonomy)
            payload = card.model_dump(mode="json")
            payload.update(
                {
                    "_search": {
                        name: getattr(article, name)
                        for name in ("title", "ai_title", "canonical_url")
                    },
                    "_source_ids": [str(origin.source_id) for origin in article.origins],
                    "_source_sort": (card.source_name or "").casefold(),
                    "_automation_blockers": blockers.get(article.id, []),
                    "_catalog_sensitive": article.review_status != "rejected"
                    and (
                        bool(article.topic_links)
                        or getattr(current.get("analysis"), "status", None) == "succeeded"
                    ),
                }
            )
            rows.append(
                dict(
                    policy_version=policy,
                    kind="article",
                    entity_id=article.id,
                    payload=payload,
                    stage=card.stage,
                    state=card.state,
                    source_id=card.source_id,
                    source_name=card.source_name,
                    search_text=searches[article.id],
                    created_at=article.discovered_at,
                    activity_at=article.discovered_at,
                    terminal_at=terminal_times[article.id],
                    has_active_work=any(
                        getattr(job, "status", None) in {"queued", "running"}
                        for job in current.values()
                    ),
                    next_transition_at=_next_transition(current.values(), now),
                    evaluated_at=now,
                )
            )
    return rows


def source_rows(factory, sources, candidates, policy, now):
    from devfeed_admin_api.source_pipeline import _activity, _card

    if not sources and not candidates:
        return []
    with factory() as session:
        source_objects = list(session.scalars(select(Source).where(Source.id.in_(sources))))
        candidate_objects = list(
            session.scalars(
                select(SourceCandidate).where(
                    SourceCandidate.id.in_(candidates),
                    SourceCandidate.source_id.is_(None),
                )
            )
        )
        facts = source_readiness_batch(session, source_objects, candidate_objects, now=now)
        rows = []
        for fact in facts.values():
            card = _card(fact)
            rows.append(
                dict(
                    policy_version=policy,
                    kind=card.kind,
                    entity_id=card.id,
                    payload=card.model_dump(mode="json"),
                    stage=card.stage,
                    state=card.state,
                    source_id=card.source_id,
                    source_name=card.name,
                    search_text=" ".join(
                        filter(None, (card.name, card.website_url, card.feed_url))
                    ),
                    created_at=card.created_at,
                    activity_at=_activity(fact),
                    terminal_at=_activity(fact)
                    if card.stage in {"active", "paused", "rejected"}
                    else None,
                    has_active_work=any(
                        job and job.status in {"queued", "running"} for job in fact.jobs.values()
                    ),
                    next_transition_at=_next_transition(fact.jobs.values(), now),
                    evaluated_at=now,
                )
            )
        return rows


def _lease(factory, token, now):
    with factory.begin() as session:
        session.execute(
            insert(ReadModelState)
            .values(name="pipeline-worker", payload={})
            .on_conflict_do_nothing()
        )
        state = session.scalar(
            select(ReadModelState)
            .where(ReadModelState.name == "pipeline-worker")
            .with_for_update(skip_locked=True)
        )
        if state is None:
            return False
        expires = state.payload.get("expires_at")
        if (
            expires
            and datetime.fromisoformat(expires) > now
            and state.payload.get("token") != token
        ):
            return False
        state.payload = {
            "token": token,
            "expires_at": (now + timedelta(seconds=LEASE_SECONDS)).isoformat(),
        }
        state.updated_at = now
    return True


def _owns_lease(session, token, now):
    state = session.scalar(
        select(ReadModelState).where(ReadModelState.name == "pipeline-worker").with_for_update()
    )
    return bool(
        state
        and state.payload.get("token") == token
        and datetime.fromisoformat(state.payload["expires_at"]) > now
    )


def _catalog_revision(session):
    return [
        [name, str(value)]
        for name, value in session.execute(
            select(CatalogRevision.name, CatalogRevision.revision)
            .where(CatalogRevision.name.in_(("topics", "tags")))
            .order_by(CatalogRevision.name)
        )
    ]


def _initialize(session, policy, now):
    state = session.get(ReadModelState, "pipeline")
    if state is None or state.payload.get("target_policy") != policy:
        payload = dict(state.payload) if state else {}
        obsolete = set(payload.get("obsolete_policies", []))
        if payload.get("target_policy"):
            obsolete.add(payload["target_policy"])
        payload.update(
            target_policy=policy,
            pending_backfills=["article", "source", "candidate"],
            backfill_revision=_catalog_revision(session),
            backfill_started_at=now.isoformat(),
            backfill_complete=False,
            backfill_progress=False,
            policy_replay_required=False,
            obsolete_policies=sorted(obsolete - {policy}),
        )
        payload.pop("catalog_refresh", None)
        set_state(session, "pipeline", payload)
        for kind in ("article", "source", "candidate"):
            enqueue_event(session, cast(EventKind, f"backfill_{kind}"))


def _expand(session, events, batch, now):
    """Expand invalidations in bounded keyset batches, never in source writes."""
    work: dict[str, set[uuid.UUID]] = {kind: set() for kind in ("article", "source", "candidate")}
    finished = set()
    acknowledged = []
    continuations: list[dict[str, Any]] = []
    scanned = 0
    state = dict(session.get(ReadModelState, "pipeline").payload)
    for event in events:
        remaining = min(batch - sum(len(values) for values in work.values()), batch - scanned)
        if remaining <= 0:
            break
        kind = event.kind
        if kind == "catalog":
            # A policy backfill already evaluates the current catalog. Changes
            # during coverage are caught by its final revision check.
            if not state.get("pending_backfills"):
                refresh = state.get("catalog_refresh")
                if event.entity_id is None:
                    if refresh is not None:
                        acknowledged.append(event.id)
                        continue
                    refresh = {
                        "token": str(uuid.uuid4()),
                        "revision": _catalog_revision(session),
                        "started_at": (event.created_at or now).isoformat(),
                    }
                    state["catalog_refresh"] = refresh
                elif refresh is None or str(event.entity_id) != refresh["token"]:
                    # A policy change or completed refresh fences old cursors.
                    acknowledged.append(event.id)
                    continue
                row = PipelineProjection
                statement = (
                    select(row.entity_id, row.payload["_catalog_sensitive"].as_boolean())
                    .where(
                        row.policy_version == state["target_policy"],
                        row.kind == "article",
                    )
                    .order_by(row.entity_id)
                    .limit(remaining)
                )
                if event.after_id:
                    statement = statement.where(row.entity_id > event.after_id)
                # Limit scanned keys before checking JSON. A sparse sensitivity
                # filter must not turn one small work batch into a full-table read.
                page = list(session.execute(statement))
                scanned += len(page)
                identifiers = [
                    identifier for identifier, sensitive in page if sensitive is not False
                ]
                work["article"].update(identifiers)
                if len(page) == remaining:
                    continuations.append(
                        dict(
                            kind="catalog",
                            entity_id=uuid.UUID(refresh["token"]),
                            after_id=page[-1][0],
                        )
                    )
                else:
                    finished.add("catalog")
        elif kind == "policy":
            if not state.get("pending_backfills"):
                state["pending_backfills"] = ["article", "source", "candidate"]
                state["backfill_revision"] = _catalog_revision(session)
                state["backfill_started_at"] = now.isoformat()
                state["backfill_complete"] = False
                state["backfill_progress"] = False
                state["policy_replay_required"] = False
                state.pop("catalog_refresh", None)
                finished.discard("catalog")
                continuations.extend(dict(kind=f"backfill_{target}") for target in work)
            elif kind == "policy" and state.get("backfill_progress", True):
                # A truncate has no row tombstones. Already covered entities need
                # another pass and a new purge cutoff after this coverage finishes.
                state["policy_replay_required"] = True
        elif kind.startswith("backfill_"):
            target = kind.removeprefix("backfill_")
            model_id = {
                "article": Article.id,
                "source": Source.id,
                "candidate": SourceCandidate.id,
            }[target]
            statement = select(model_id).order_by(model_id).limit(remaining)
            if event.after_id:
                statement = statement.where(model_id > event.after_id)
            identifiers = list(session.scalars(statement))
            work[target].update(identifiers)
            if len(identifiers) == remaining:
                continuations.append(dict(kind=kind, after_id=identifiers[-1]))
            else:
                finished.add(target)
        elif kind in {"source", "source_fanout"}:
            assert event.entity_id is not None
            if kind == "source":
                work["source"].add(event.entity_id)
                acknowledged.append(event.id)
                continue
            statement = (
                select(ArticleOrigin.article_id)
                .where(ArticleOrigin.source_id == event.entity_id)
                .distinct()
                .order_by(ArticleOrigin.article_id)
                .limit(remaining)
            )
            if event.after_id:
                statement = statement.where(ArticleOrigin.article_id > event.after_id)
            identifiers = list(session.scalars(statement))
            work["article"].update(identifiers)
            if len(identifiers) == remaining:
                continuations.append(
                    dict(kind="source_fanout", entity_id=event.entity_id, after_id=identifiers[-1])
                )
        else:
            assert event.entity_id is not None
            work[kind].add(event.entity_id)
        acknowledged.append(event.id)
    return work, finished, acknowledged, continuations, state


def _publish_rows(session, work, rows, policy, *, refresh_evaluated_at):
    """Publish changed cards without rewriting unchanged completed coverage."""
    produced = {(row["kind"], row["entity_id"]) for row in rows}
    for kind, identifiers in work.items():
        tombstones = {
            identifier for identifier in identifiers if (kind, identifier) not in produced
        }
        if tombstones:
            session.execute(
                delete(PipelineProjection).where(
                    PipelineProjection.policy_version == policy,
                    PipelineProjection.kind == kind,
                    PipelineProjection.entity_id.in_(tombstones),
                )
            )
    if not rows:
        return
    table = PipelineProjection.__table__
    statement = insert(PipelineProjection).values(rows)
    fields = [column.name for column in table.columns if not column.primary_key]
    semantic_fields = [name for name in fields if name != "evaluated_at"]
    changed = tuple_(*(table.c[name] for name in semantic_fields)).is_distinct_from(
        tuple_(*(statement.excluded[name] for name in semantic_fields))
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                PipelineProjection.policy_version,
                PipelineProjection.kind,
                PipelineProjection.entity_id,
            ],
            set_={name: statement.excluded[name] for name in fields},
            # Full coverage must advance its purge cutoff even for identical cards.
            where=None if refresh_evaluated_at else changed,
        )
    )


def project_batch(factory, *, batch=200, token=None, now=None):
    """Publish one bounded batch; a crash or expired lease leaves work retryable."""
    now, token = now or utcnow(), token or str(uuid.uuid4())
    if not _lease(factory, token, now):
        return 0
    policy = pipeline_policy_version()
    try:
        removed = 0
        with factory.begin() as session:
            _initialize(session, policy, now)
            # The due timestamp is cleared transactionally with queuing, preventing
            # a slow backlog from scheduling the same timer on every worker tick.
            due = list(
                session.scalars(
                    select(PipelineProjection)
                    .where(
                        PipelineProjection.policy_version == policy,
                        PipelineProjection.next_transition_at <= now,
                    )
                    .order_by(PipelineProjection.next_transition_at)
                    .limit(batch)
                )
            )
            for row in due:
                enqueue_event(session, row.kind, row.entity_id)
                row.next_transition_at = None
            events = event_batch(session, batch)
            # Expansion only reads. Continuations and acknowledgement commit
            # atomically with the computed rows, including after a worker crash.
            work, finished, event_ids, continuations, planned_state = _expand(
                session, events, batch, now
            )
        rows = []
        article_batch = min(batch, 200)
        for offset in range(0, len(work["article"]), article_batch):
            rows.extend(
                article_rows(
                    factory,
                    sorted(work["article"])[offset : offset + article_batch],
                    policy,
                    now,
                )
            )
        rows.extend(
            source_rows(factory, list(work["source"]), list(work["candidate"]), policy, now)
        )
        with factory.begin() as session:
            if not _owns_lease(session, token, utcnow()):
                return 0
            _publish_rows(
                session,
                work,
                rows,
                policy,
                refresh_evaluated_at=not planned_state.get("backfill_complete"),
            )
            for continuation in continuations:
                enqueue_event(session, **continuation)
            acknowledge_events(session, event_ids)
            payload = planned_state
            if payload.get("pending_backfills") and any(work.values()):
                payload["backfill_progress"] = True
            pending = set(payload.get("pending_backfills", [])) - finished
            payload["pending_backfills"] = sorted(pending)
            # Event queues are coalesced at processing, not by global ID watermarks.
            remaining_backfills = None
            if not pending and not payload.get("backfill_complete"):
                remaining_backfills = session.scalar(
                    select(ReadModelEvent.id)
                    .where(
                        ReadModelEvent.kind.in_(
                            ("backfill_article", "backfill_source", "backfill_candidate")
                        ),
                    )
                    .limit(1)
                )
            if not pending and remaining_backfills is None and not payload.get("backfill_complete"):
                # Coverage and catalog freshness are separate. Only a global
                # policy invalidation needs another population-wide coverage pass.
                started = datetime.fromisoformat(payload["backfill_started_at"])
                obsolete = list(
                    session.execute(
                        select(
                            PipelineProjection.kind,
                            PipelineProjection.entity_id,
                        )
                        .where(
                            PipelineProjection.policy_version == policy,
                            PipelineProjection.evaluated_at < started,
                        )
                        .limit(batch)
                    )
                )
                if obsolete:
                    session.execute(
                        delete(PipelineProjection).where(
                            PipelineProjection.policy_version == policy,
                            tuple_(PipelineProjection.kind, PipelineProjection.entity_id).in_(
                                obsolete
                            ),
                        )
                    )
                removed = len(obsolete)
                if not removed:
                    payload["serving_policy"] = policy
                    payload["serving_generated_at"] = now.isoformat()
                    current_revision = _catalog_revision(session)
                    payload["backfill_complete"] = not payload.get("policy_replay_required")
                    if not payload["backfill_complete"]:
                        payload["pending_backfills"] = ["article", "source", "candidate"]
                        payload["backfill_revision"] = current_revision
                        payload["backfill_started_at"] = utcnow().isoformat()
                        payload["backfill_progress"] = False
                        payload["policy_replay_required"] = False
                        for kind in ("article", "source", "candidate"):
                            enqueue_event(session, cast(EventKind, f"backfill_{kind}"))
                    else:
                        payload["article_generated_at"] = now.isoformat()
                        payload["source_generated_at"] = now.isoformat()
                        if payload.get("backfill_revision") != current_revision:
                            enqueue_event(session, "catalog")
            if "catalog" in finished:
                refresh = payload.pop("catalog_refresh")
                if refresh["revision"] != _catalog_revision(session):
                    # Coalesce edits observed during this bounded refresh. They
                    # never restart historical/source/candidate coverage.
                    enqueue_event(session, "catalog")
            if payload.get("serving_policy") == policy:
                if work["article"]:
                    payload["article_generated_at"] = now.isoformat()
                if work["source"] or work["candidate"]:
                    payload["source_generated_at"] = now.isoformat()
                if payload.get("backfill_complete"):
                    payload["serving_generated_at"] = now.isoformat()
                old_keys = list(
                    session.execute(
                        select(
                            PipelineProjection.policy_version,
                            PipelineProjection.kind,
                            PipelineProjection.entity_id,
                        )
                        .where(
                            PipelineProjection.policy_version.in_(
                                payload.get("obsolete_policies", [])
                            )
                        )
                        .limit(batch)
                    )
                )
                if old_keys:
                    session.execute(
                        delete(PipelineProjection).where(
                            tuple_(
                                PipelineProjection.policy_version,
                                PipelineProjection.kind,
                                PipelineProjection.entity_id,
                            ).in_(old_keys)
                        )
                    )
                    removed += len(old_keys)
                else:
                    payload["obsolete_policies"] = []
                expired = list(
                    session.execute(
                        select(PipelineProjection.entity_id)
                        .where(
                            PipelineProjection.policy_version == policy,
                            PipelineProjection.kind == "article",
                            PipelineProjection.stage.in_(("published", "rejected")),
                            PipelineProjection.terminal_at < now - timedelta(days=7),
                        )
                        .order_by(PipelineProjection.terminal_at)
                        .limit(batch)
                    ).scalars()
                )
                if expired:
                    session.execute(
                        delete(PipelineProjection).where(
                            PipelineProjection.policy_version == policy,
                            PipelineProjection.kind == "article",
                            PipelineProjection.entity_id.in_(expired),
                        )
                    )
                    removed += len(expired)
            set_state(session, "pipeline", payload)
        return len(event_ids) + removed
    finally:
        with factory.begin() as session:
            if _owns_lease(session, token, utcnow()):
                session.execute(
                    update(ReadModelState)
                    .where(ReadModelState.name == "pipeline-worker")
                    .values(payload={}, updated_at=utcnow())
                )


@reuse_snapshots()
@reuse_candidates()
def drain_pipeline(factory, *, batch=200, max_batches=10000):
    """Operator/backfill entry point; never called by an HTTP request."""
    count = 0
    for _ in range(max_batches):
        processed = project_batch(factory, batch=batch)
        count += processed
        if not processed:
            with factory() as session:
                state = session.get(ReadModelState, "pipeline")
                pending = session.scalar(select(ReadModelEvent.id).limit(1))
                if pending is not None or not state or not state.payload.get("backfill_complete"):
                    raise RuntimeError(
                        "Pipeline backfill is busy or its publisher lease expired; retry the drain"
                    )
            return count
    raise RuntimeError("Pipeline projection has not drained within its bounded run")

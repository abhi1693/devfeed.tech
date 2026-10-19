"""An operator view of the same readiness checks used by publication admission.

Stages are derived from durable jobs and article data, never a second workflow
status that operators must keep in sync. Active work includes all ages; terminal
lanes use an explicit time window. Batch reads keep database work independent of
the number of cards displayed and avoid loading historical inference runs.
"""

import hashlib
import json
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from itertools import chain, islice
from threading import Lock
from typing import Annotated, Literal

from devfeed_core.analysis import PROMPT_VERSION, catalog
from devfeed_core.article_analysis_steps import STEP_SPECS
from devfeed_core.article_jobs import approved_sources, request_article_enrichment
from devfeed_core.article_lifecycle import (
    ProcessingReadiness,
    derive_publication_readiness,
    publication_readiness,
    reconcile_processing,
)
from devfeed_core.article_processing_retry import (
    RetryTarget,
    current_processing_job,
    processing_retry_targets,
    request_processing_retry,
    retry_readiness,
)
from devfeed_core.config import get_settings
from devfeed_core.job_retries import retry_candidate
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleReview,
    ArticleTopic,
    Source,
    Topic,
    utcnow,
)
from devfeed_core.schemas import DatabaseText, InputModel, ORMModel
from devfeed_core.services import OperationConflict
from devfeed_core.text_search import text_search
from devfeed_http.pagination import record
from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import case, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import defer, lazyload, load_only, selectinload

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB

Stage = Literal[
    "ingested", "enrichment", "analysis", "images", "review", "ready", "published", "rejected"
]
STAGES: tuple[Stage, ...] = (
    "ingested",
    "enrichment",
    "analysis",
    "images",
    "review",
    "ready",
    "published",
    "rejected",
)
BATCH_SIZE = 200
_readiness_cache: OrderedDict[tuple, ProcessingReadiness] = OrderedDict()
_readiness_lock = Lock()
_CACHE_SIZE = 4096
router = APIRouter(tags=["admin-articles"], dependencies=[Depends(require_admin)])


class PipelineJob(ORMModel):
    id: uuid.UUID
    status: str
    outcome: str | None
    available_at: datetime
    finished_at: datetime | None
    retryable: bool


class PipelineJobs(ORMModel):
    enrichment: PipelineJob | None = None
    analysis: PipelineJob | None = None
    images: PipelineJob | None = None


class PipelineCard(ORMModel):
    id: uuid.UUID
    title: str
    source_name: str | None
    source_id: uuid.UUID | None
    discovered_at: datetime
    stage: Stage
    state: str
    blockers: list[str]
    next_check_at: datetime | None
    editorial_revision: int
    jobs: PipelineJobs
    allowed_actions: list[str] = Field(default_factory=list)
    current_analysis_step: str | None = None
    retry_targets: list[RetryTarget] = Field(default_factory=list)


class PipelineSourceGroup(ORMModel):
    source_id: uuid.UUID | None
    source_name: str | None
    total: int
    state_counts: dict[str, int]


class PipelineColumn(ORMModel):
    stage: Stage
    total: int
    items: list[PipelineCard]
    next_cursor: int | None
    source_groups: list[PipelineSourceGroup] = Field(default_factory=list)


class PipelineBoard(ORMModel):
    as_of: datetime
    terminal_since: datetime
    counts: dict[str, int]
    columns: list[PipelineColumn]


class PipelineCheck(ORMModel):
    code: str
    label: str
    status: Literal["passed", "pending", "blocked"]
    detail: str


class AnalysisStepOut(ORMModel):
    name: str
    label: str
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    prompt_version: str
    attempts: int = Field(ge=0)
    error: str | None = None
    available_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PipelineDetail(ORMModel):
    article_id: uuid.UUID
    editorial_revision: int
    stage: Stage
    state: str
    checks: list[PipelineCheck]
    blockers: list[str]
    can_publish: bool
    jobs: PipelineJobs
    allowed_actions: list[str]
    advance_label: str | None
    analysis_steps: list[AnalysisStepOut] = Field(default_factory=list)
    retry_targets: list[RetryTarget] = Field(default_factory=list)


class AdvancePipeline(InputModel):
    expected_revision: int = Field(ge=0)


class RetryPipeline(InputModel):
    expected_revision: int = Field(ge=0)
    target: RetryTarget
    expected_job_id: uuid.UUID | None


def _display_source_id():
    """Match the source attribution displayed by _card, including missing origins."""
    return (
        select(Source.id)
        .join(ArticleOrigin, ArticleOrigin.source_id == Source.id)
        .where(ArticleOrigin.article_id == Article.id)
        .order_by(Source.approval_status != "approved", Source.name.collate("C"), Source.id)
        .limit(1)
        .correlate(Article)
        .scalar_subquery()
    )


def _statement(q, source_id, source_group=None):
    statement = select(Article).options(
        lazyload(Article.tags),
        selectinload(Article.origins)
        .load_only(ArticleOrigin.source_id, ArticleOrigin.article_id, raiseload=True)
        .joinedload(ArticleOrigin.source)
        .load_only(Source.id, Source.name, Source.approval_status, raiseload=True),
        selectinload(Article.topic_links)
        .load_only(ArticleTopic.topic_id, ArticleTopic.role, raiseload=True)
        .joinedload(ArticleTopic.topic)
        .load_only(Topic.id, Topic.status, raiseload=True),
    )
    if q:
        statement = statement.where(
            text_search(q, Article.title, Article.ai_title, Article.canonical_url)
        )
    if source_id:
        statement = statement.where(
            select(ArticleOrigin.id)
            .where(ArticleOrigin.article_id == Article.id, ArticleOrigin.source_id == source_id)
            .exists()
        )
    if source_group is not None:
        displayed = _display_source_id()
        statement = statement.where(
            displayed.is_(None) if source_group == "unknown" else displayed == source_group
        )
    return statement


def _latest(session, model, identifiers, *, operation=None, lightweight=False):
    current = select(model.id).where(model.article_id.in_(identifiers))
    order = [model.article_id, model.status.in_(["queued", "running"]).desc()]
    if operation:
        current = current.where(model.operation == operation)
        current = current.join(Article, Article.id == model.article_id)
        current_input = (
            model.image_url.is_not_distinct_from(Article.image_url)
            if operation == "store"
            else (model.storage["input_url"].as_string() == Article.canonical_url).is_(True)
        )
        order.append(current_input.desc())
    current = current.ext(distinct_on(model.article_id)).order_by(
        *order,
        model.created_at.desc(),
        model.id.desc(),
    )
    statement = select(model, retry_candidate(model)).where(model.id.in_(current))
    if lightweight and model is ArticleAnalysisJob:
        pending_step = case(
            *(
                (
                    func.coalesce(
                        model.usage["article_steps"][name]["status"].as_string(), "pending"
                    ).not_in(["succeeded", "skipped"]),
                    literal(name),
                )
                for name in STEP_SPECS
            ),
            else_=None,
        )
        progress = case(
            (
                model.status.in_(["queued", "running", "failed"])
                & (model.prompt_version == PROMPT_VERSION),
                func.coalesce(model.usage["active_step"].as_string(), pending_step),
            ),
            else_=None,
        )
        statement = statement.add_columns(progress).options(
            defer(model.input_snapshot), defer(model.catalog_snapshot), defer(model.usage)
        )
    result = {}
    for row in session.execute(statement.options(lazyload("*"))):
        job, eligible = row[:2]
        job._pipeline_retryable = eligible
        if lightweight and model is ArticleAnalysisJob:
            job._pipeline_current_step = row[2]
        result[job.article_id] = job
    return result


def _batch_facts(session, articles, *, lightweight=False):
    identifiers = [article.id for article in articles]
    content_query = select(ArticleContent).where(ArticleContent.article_id.in_(identifiers))
    if lightweight:
        content_query = content_query.options(
            load_only(
                ArticleContent.article_id,
                ArticleContent.content_hash,
                ArticleContent.method,
                ArticleContent.url,
            )
        )
    contents = {content.article_id: content for content in session.scalars(content_query)}
    return {
        "content": contents,
        "enrichment": _latest(session, ArticleEnrichmentJob, identifiers),
        "analysis": _latest(session, ArticleAnalysisJob, identifiers, lightweight=lightweight),
        "discovery": _latest(session, ArticleImageJob, identifiers, operation="discover"),
        "storage": _latest(session, ArticleImageJob, identifiers, operation="store"),
    }


def _readiness_key(article, facts, *, catalog_hash, settings, now):
    """Identity of every admission input, excluding immutable large evidence.

    Content text is identified by its extraction hash; immutable inference input
    and candidate snapshots by their stored hashes. The cache is only a display
    optimization. Publication always recomputes readiness in its locked transaction.
    """
    values = {
        "article": {
            field: getattr(article, field)
            for field in (
                "canonical_url",
                "title",
                "summary",
                "ai_title",
                "ai_summary",
                "ai_description",
                "review_status",
                "publication_status",
                "language",
                "content_type",
                "content_format",
                "classification_provenance",
                "metadata_source_type",
                "published_at",
                "image_url",
                "managed_image",
            )
        },
        "sources": sorted(
            (str(origin.source_id), origin.source.approval_status) for origin in article.origins
        ),
        "topics": sorted(
            (str(link.topic_id), link.role, link.topic.status) for link in article.topic_links
        ),
        "catalog": catalog_hash,
        "settings": (
            settings.ai_enabled,
            settings.image_storage_enabled,
            settings.image_public_url,
            settings.analysis_max_candidates,
            settings.analysis_fallback_candidates,
            settings.ai_content_not_before,
        ),
    }
    content = facts["content"]
    values["content"] = (content.content_hash, content.method, content.url) if content else None
    for name, job in facts.items():
        if name == "content":
            continue
        values[name] = (
            None
            if job is None
            else {
                field: getattr(job, field)
                for field in (
                    "id",
                    "status",
                    "outcome",
                    "error",
                    "available_at",
                    "attempts",
                    *(
                        (
                            "input_hash",
                            "catalog_hash",
                            "prompt_version",
                            "result",
                            "inputs_pruned_at",
                        )
                        if name == "analysis"
                        else ("result",)
                        if name == "enrichment"
                        else ("image_url", "storage")
                    ),
                )
            }
        )
        if job is not None:
            values[name]["future"] = job.available_at > now
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).digest()
    return article.id, digest


def _batch_readiness(session, articles, *, taxonomy, settings, now):
    facts = _batch_facts(session, articles, lightweight=True)
    catalog_hash = (
        hashlib.sha256(json.dumps(taxonomy, sort_keys=True, default=str).encode()).digest().hex()
    )
    keys = {}
    results = {}
    misses = []
    for article in articles:
        current = {name: rows.get(article.id) for name, rows in facts.items()}
        key = keys[article.id] = _readiness_key(
            article, current, catalog_hash=catalog_hash, settings=settings, now=now
        )
        with _readiness_lock:
            cached = _readiness_cache.get(key)
            if cached is not None:
                _readiness_cache.move_to_end(key)
        if cached is not None:
            results[article.id] = cached
        else:
            misses.append(article)
    if misses:
        ids = [article.id for article in misses]
        # Hydrate private evidence in batches only when its identity changed.
        content_ids = [identifier for identifier in ids if identifier in facts["content"]]
        analysis_ids = [
            facts["analysis"][identifier].id
            for identifier in ids
            if identifier in facts["analysis"]
        ]
        if content_ids:
            list(
                session.scalars(
                    select(ArticleContent).where(ArticleContent.article_id.in_(content_ids))
                )
            )
        if analysis_ids:
            list(
                session.scalars(
                    select(ArticleAnalysisJob).where(ArticleAnalysisJob.id.in_(analysis_ids))
                )
            )
        for article in misses:
            current = {name: rows.get(article.id) for name, rows in facts.items()}
            readiness = derive_publication_readiness(
                article, **current, taxonomy=taxonomy, settings=settings, now=now
            )
            results[article.id] = readiness
            with _readiness_lock:
                _readiness_cache[keys[article.id]] = readiness
                while len(_readiness_cache) > _CACHE_SIZE:
                    _readiness_cache.popitem(last=False)
    return facts, results


def _job(job, *, retryable=True):
    if job is None:
        return None
    return PipelineJob(
        id=job.id,
        status=job.status,
        outcome=job.outcome,
        available_at=job.available_at,
        finished_at=job.finished_at,
        retryable=job.status == "failed"
        and retryable
        and bool(getattr(job, "_pipeline_retryable", False)),
    )


def _jobs(facts, *, allow_retry=True, readiness=None):
    latest_image = current_processing_job(facts, "images", readiness=readiness)
    return PipelineJobs(
        enrichment=_job(facts["enrichment"], retryable=allow_retry),
        analysis=_job(facts["analysis"], retryable=allow_retry and get_settings().ai_enabled),
        images=_job(latest_image, retryable=allow_retry),
    )


_STEP_LABELS = {
    "developer_relevance": "Developer relevance",
    "page_kind": "Article eligibility",
    "language": "Source language",
    "content_type": "Content type",
    "content_format": "Content format",
    "topics": "Topic matching",
    "tags": "Tag matching",
    "identifying_title": "Identifying title",
    "overview": "Article overview",
    "description": "Search description",
}


def _step_time(value):
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _analysis_steps(job):
    if job is None or (not job.steps and job.prompt_version != PROMPT_VERSION):
        return []
    terminal = job.status == "succeeded"
    rows = []
    for name, spec in STEP_SPECS.items():
        step = job.steps.get(name, {})
        status = step.get("status", "skipped" if terminal else "pending")
        if status not in {"pending", "running", "succeeded", "failed", "skipped"}:
            status = "pending"
        error = step.get("error")
        # Worker records contain safe reason codes, never a provider response.
        # Retain that boundary when presenting malformed/legacy step metadata.
        if error is not None and (
            not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", error)
        ):
            error = "step_failed"
        rows.append(
            AnalysisStepOut(
                name=name,
                label=_STEP_LABELS[name],
                status=status,
                prompt_version=step.get("prompt_version", spec.prompt_version),
                attempts=max(0, step.get("attempts", 0)),
                error=error,
                available_at=_step_time(step.get("available_at"))
                or (
                    job.available_at if job.status == "queued" and name == job.active_step else None
                ),
                started_at=_step_time(step.get("started_at")),
                finished_at=_step_time(step.get("finished_at")),
            )
        )
    return rows


def _current_analysis_step(job):
    if job is None or job.status not in {"queued", "running", "failed"}:
        return None
    if hasattr(job, "_pipeline_current_step"):
        value = job._pipeline_current_step
        return value if value in STEP_SPECS else None
    if job.active_step in STEP_SPECS:
        return job.active_step
    steps = _analysis_steps(job)
    for status in ("running", "failed", "pending"):
        current = next((step.name for step in steps if step.status == status), None)
        if current:
            return current
    return None


def _actions(readiness):
    if "no_approved_source" in readiness.blockers:
        return []
    if readiness.stage not in {"ingested", "enrichment", "analysis", "images"}:
        return []
    key = "enrichment" if readiness.stage == "ingested" else readiness.stage
    if key == "analysis" and {
        "content_date_deferred",
        "ai_not_configured",
        "insufficient_analysis_evidence",
        "manual_analysis_review_required",
    }.intersection(readiness.blockers):
        return []
    check = readiness.checks.get(key)
    if (
        key == "enrichment"
        and check is not None
        and check.status == "complete"
        and "insufficient_source_text" in readiness.blockers
    ):
        return ["advance"]
    if (
        check is not None
        and check.status in {"missing", "stale"}
        and (key != "analysis" or get_settings().ai_enabled)
    ):
        return ["advance"]
    return []


def _card(article, readiness, facts, *, taxonomy=None):
    sources = sorted(
        (origin.source for origin in article.origins),
        key=lambda source: (source.approval_status != "approved", source.name, source.id),
    )
    source = sources[0] if sources else None
    processing = retry_readiness(article, readiness, facts, taxonomy=taxonomy)
    return PipelineCard(
        id=article.id,
        title=article.ai_title or article.title,
        source_name=source.name if source else None,
        source_id=source.id if source else None,
        discovered_at=article.discovered_at,
        stage=readiness.stage,
        state=readiness.state,
        blockers=readiness.blockers,
        editorial_revision=article.editorial_revision,
        next_check_at=article.automation_next_check_at if readiness.state != "complete" else None,
        jobs=_jobs(facts, readiness=processing),
        allowed_actions=_actions(readiness),
        current_analysis_step=_current_analysis_step(facts["analysis"]),
        retry_targets=processing_retry_targets(article, processing, facts),
    )


def _active_cards(session, statement, *, taxonomy, settings, now):
    """Read each waiting article once, bounded in memory, with no per-card queries.

    Exact catalog/source freshness uses the shared Python admission contract. We
    intentionally do not duplicate that policy in approximate SQL lane counts.
    SQL keyset batches bound memory and only current job rows are retrieved.
    """
    after = None
    while True:
        batch_query = statement.where(
            Article.publication_status == "unpublished", Article.review_status != "rejected"
        )
        if after:
            batch_query = batch_query.where(tuple_(Article.discovered_at, Article.id) > after)
        batch = list(
            session.scalars(
                batch_query.order_by(Article.discovered_at, Article.id).limit(BATCH_SIZE)
            )
        )
        if not batch:
            return
        facts, readiness_by_id = _batch_readiness(
            session, batch, taxonomy=taxonomy, settings=settings, now=now
        )
        for article in batch:
            current = {key: values.get(article.id) for key, values in facts.items()}
            readiness = readiness_by_id[article.id]
            yield _card(article, readiness, current, taxonomy=taxonomy)
        after = (batch[-1].discovered_at, batch[-1].id)
        # Do not retain ORM instances/evidence for the entire waiting population.
        session.expunge_all()


def _source_group_order(group):
    priority = (
        0
        if group.state_counts.get("running", 0)
        else 1
        if group.state_counts.get("blocked", 0) or group.state_counts.get("retry_wait", 0)
        else 2
    )
    return (
        priority,
        group.source_name is None,
        (group.source_name or "").casefold(),
        str(group.source_id or ""),
    )


def _terminal_column(
    session,
    statement,
    stage,
    *,
    since,
    limit,
    cursor,
    needs_attention,
    taxonomy,
    group_by=None,
):
    terminal_at = (
        Article.published_to_feed_at
        if stage == "published"
        else func.coalesce(
            select(func.max(ArticleReview.created_at))
            .where(ArticleReview.article_id == Article.id, ArticleReview.action == "reject")
            .correlate(Article)
            .scalar_subquery(),
            Article.discovered_at,
        )
    )
    terminal = statement.where(
        Article.publication_status == "published"
        if stage == "published"
        else Article.review_status == "rejected",
        terminal_at >= since,
    )
    if group_by == "source":
        displayed = terminal.with_only_columns(
            _display_source_id().label("source_id"), maintain_column_froms=True
        ).subquery()
        grouped = (
            select(displayed.c.source_id, Source.name, func.count())
            .select_from(displayed)
            .outerjoin(Source, Source.id == displayed.c.source_id)
            .group_by(displayed.c.source_id, Source.name)
        )
        groups = (
            []
            if needs_attention
            else [
                PipelineSourceGroup(
                    source_id=identifier,
                    source_name=name,
                    total=count,
                    state_counts={"complete": count},
                )
                for identifier, name, count in session.execute(grouped)
            ]
        )
        groups.sort(key=_source_group_order)
        return PipelineColumn(
            stage=stage,
            total=sum(group.total for group in groups),
            items=[],
            source_groups=groups[cursor : cursor + limit],
            next_cursor=cursor + limit if cursor + limit < len(groups) else None,
        )
    total = (
        0
        if needs_attention
        else session.scalar(select(func.count()).select_from(terminal.subquery())) or 0
    )
    articles = (
        []
        if needs_attention
        else list(
            session.scalars(
                terminal.order_by(terminal_at.desc(), Article.id.desc()).offset(cursor).limit(limit)
            )
        )
    )
    cards = []
    if articles:
        # Published retry controls need their prospective processing prerequisites.
        batch = _batch_facts(session, articles) if stage == "published" else None
        empty = {key: None for key in ("content", "enrichment", "analysis", "discovery", "storage")}
        for article in articles:
            readiness = derive_publication_readiness(article)
            facts = (
                {key: values.get(article.id) for key, values in batch.items()} if batch else empty
            )
            cards.append(_card(article, readiness, facts, taxonomy=taxonomy))
    return PipelineColumn(
        stage=stage,
        total=total,
        items=cards,
        next_cursor=cursor + limit if cursor + limit < total else None,
    )


def _board(
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
    now = utcnow()
    since = now - timedelta(days=7)
    statement = _statement(q, source_id, source_group)
    settings = get_settings()
    taxonomy = catalog(session)
    stages = (stage,) if stage else STAGES
    columns = {
        value: PipelineColumn(stage=value, total=0, items=[], next_cursor=None) for value in stages
    }
    if stage not in {"published", "rejected"}:
        start = cursor if stage else 0
        end = start + limit
        running: dict[Stage, list[PipelineCard]] = {value: [] for value in stages}
        other: dict[Stage, list[PipelineCard]] = {value: [] for value in stages}
        groups: dict[Stage, dict[uuid.UUID | None, PipelineSourceGroup]] = {
            value: {} for value in stages
        }
        for card in _active_cards(
            session, statement, taxonomy=taxonomy, settings=settings, now=now
        ):
            if card.stage not in columns or (
                needs_attention and card.state not in {"blocked", "retry_wait"}
            ):
                continue
            column = columns[card.stage]
            column.total += 1
            if group_by == "source":
                group = groups[card.stage].get(card.source_id)
                if group is None:
                    group = groups[card.stage][card.source_id] = PipelineSourceGroup(
                        source_id=card.source_id,
                        source_name=card.source_name,
                        total=0,
                        state_counts={},
                    )
                group.total += 1
                group.state_counts[card.state] = group.state_counts.get(card.state, 0) + 1
                continue
            active, remaining = running[card.stage], other[card.stage]
            # The stream is FIFO. Partition the complete column before paging,
            # retaining only its first cursor + limit cards in the new order.
            if card.state == "running":
                if len(active) < end:
                    active.append(card)
                if len(active) + len(remaining) > end:
                    remaining.pop()
            elif len(active) + len(remaining) < end:
                remaining.append(card)
        for column in columns.values():
            if group_by == "source":
                ordered = sorted(groups[column.stage].values(), key=_source_group_order)
                column.source_groups = ordered[start:end]
                column.next_cursor = end if end < len(ordered) else None
                continue
            column.items = list(
                islice(chain(running[column.stage], other[column.stage]), start, end)
            )
            column.next_cursor = start + limit if start + limit < column.total else None
    for terminal in ("published", "rejected"):
        if terminal in columns:
            columns[terminal] = _terminal_column(
                session,
                statement,
                terminal,
                since=since,
                limit=limit,
                cursor=cursor if stage else 0,
                needs_attention=needs_attention,
                taxonomy=taxonomy,
                group_by=group_by,
            )
    return PipelineBoard(
        as_of=now,
        terminal_since=since,
        counts={key: value.total for key, value in columns.items()},
        columns=list(columns.values()),
    )


@router.get(
    "/v1/admin/article-pipeline",
    response_model=PipelineBoard,
    operation_id="admin_article_pipeline",
)
def board(
    session: DB,
    q: Annotated[DatabaseText | None, Query(max_length=200)] = None,
    source_id: uuid.UUID | None = None,
    needs_attention: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    group_by: Literal["source"] | None = None,
):
    result = _board(
        session,
        q=q,
        source_id=source_id,
        needs_attention=needs_attention,
        limit=limit,
        group_by=group_by,
    )
    session.close()
    return result


@router.get(
    "/v1/admin/article-pipeline/{stage}",
    response_model=PipelineColumn,
    operation_id="admin_article_pipeline_column",
)
def column(
    stage: Stage,
    session: DB,
    q: Annotated[DatabaseText | None, Query(max_length=200)] = None,
    source_id: uuid.UUID | None = None,
    needs_attention: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
    group_by: Literal["source"] | None = None,
    source_group: uuid.UUID | Literal["unknown"] | None = None,
):
    result = _board(
        session,
        q=q,
        source_id=source_id,
        needs_attention=needs_attention,
        limit=limit,
        stage=stage,
        cursor=cursor,
        group_by=group_by,
        source_group=source_group,
    ).columns[0]
    session.close()
    return result


def _detail(session, article):
    batch = _batch_facts(session, [article])
    facts = {key: values.get(article.id) for key, values in batch.items()}
    taxonomy = catalog(session)
    readiness = derive_publication_readiness(article, **facts, taxonomy=taxonomy)
    processing = retry_readiness(article, readiness, facts, taxonomy=taxonomy)
    checks = [
        PipelineCheck(
            code=code,
            label={
                "enrichment": "Source evidence",
                "analysis": "Current AI analysis",
                "images": "Article images",
            }[code],
            status="passed"
            if check.status == "complete" or (code == "images" and check.status == "unavailable")
            else "blocked"
            if check.status in {"failed", "stale", "unavailable", "disabled"}
            else "pending",
            detail=(
                "No article image found; the reader fallback is available"
                if code == "images" and check.status == "unavailable"
                else check.error or check.outcome or check.status.replace("_", " ")
            ),
        )
        for code, check in readiness.checks.items()
    ]
    if readiness.stage not in {"published", "rejected"}:
        requirements = (
            ("identifying_title", "Identifying title", {"missing_title", "unidentifiable_title"}),
            ("overview", "Article overview", {"missing_ai_summary"}),
            ("description", "Search description", {"missing_ai_description"}),
            ("language", "Known language", {"unknown_language"}),
            ("primary_topic", "Active primary topic", {"missing_active_primary_topic"}),
            (
                "relevance",
                "Relevant article",
                {"developer_relevance_unresolved", "not_substantive_article"},
            ),
            ("source", "Approved source", {"no_approved_source"}),
            (
                "metadata",
                "Valid URL and content format",
                {"invalid_canonical_url", "unknown_content_type", "unknown_content_format"},
            ),
            ("approval", "Editorial approval", {"not_approved"}),
        )
        for code, label, reasons in requirements:
            missing = reasons.intersection(readiness.blockers)
            checks.append(
                PipelineCheck(
                    code=code,
                    label=label,
                    status="pending"
                    if missing == {"not_approved"}
                    else "blocked"
                    if missing
                    else "passed",
                    detail="; ".join(reason.replace("_", " ") for reason in sorted(missing))
                    if missing
                    else "Complete",
                )
            )
        extra = {
            "content_date_deferred": (
                "Content date",
                "The source publication date is outside the configured AI content window",
            ),
            "insufficient_source_text": (
                "Source text",
                "No readable source text is available. "
                "Fetch the source again or inspect its evidence.",
            ),
            "insufficient_analysis_evidence": (
                "Analysis evidence",
                "The completed analysis could not establish the required evidence. "
                "Inspect the source and classification.",
            ),
            "ai_not_configured": (
                "AI configuration",
                "Configure and enable AI to produce the required article content",
            ),
            "paywalled_content": (
                "Source access",
                "The publisher gates this article behind a paywall",
            ),
        }
        for code, (label, description) in extra.items():
            if code in readiness.blockers:
                checks.append(
                    PipelineCheck(code=code, label=label, status="blocked", detail=description)
                )
    actions = _actions(readiness)
    labels = {
        "ingested": "Start enrichment",
        "enrichment": "Run enrichment",
        "analysis": "Run current analysis",
        "images": "Process images",
    }
    if readiness.stage == "enrichment" and "insufficient_source_text" in readiness.blockers:
        labels["enrichment"] = "Fetch source again"
    return PipelineDetail(
        article_id=article.id,
        editorial_revision=article.editorial_revision,
        stage=readiness.stage,
        state=readiness.state,
        checks=checks,
        blockers=readiness.blockers,
        can_publish=readiness.stage == "ready" and not readiness.blockers,
        jobs=_jobs(
            facts,
            allow_retry=readiness.stage not in {"published", "rejected"},
            readiness=processing,
        ),
        allowed_actions=actions,
        advance_label=labels.get(readiness.stage) if actions else None,
        analysis_steps=_analysis_steps(facts["analysis"]),
        retry_targets=processing_retry_targets(article, processing, facts),
    )


@router.get(
    "/v1/admin/articles/{article_id}/pipeline",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_detail",
)
def detail(article_id: uuid.UUID, session: DB):
    result = _detail(session, record(session, Article, article_id))
    session.close()
    return result


@router.post(
    "/v1/admin/articles/{article_id}/pipeline/advance",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_advance",
)
def advance(article_id: uuid.UUID, body: AdvancePipeline, session: DB, admin: Admin):
    approved_sources(session, article_id, lock=True)
    article = record(session, Article, article_id, lock=True)
    if article.editorial_revision != body.expected_revision:
        raise OperationConflict("Article changed; reload before resuming processing")
    readiness = publication_readiness(session, article)
    if "advance" not in _actions(readiness):
        raise OperationConflict("No processing step can be resumed; refresh the readiness checks")
    if readiness.stage == "enrichment" and "insufficient_source_text" in readiness.blockers:
        request_article_enrichment(session, article_id)
    else:
        reconcile_processing(session, article)
    session.commit()
    result = _detail(session, article)
    session.close()
    return result


@router.post(
    "/v1/admin/articles/{article_id}/pipeline/retry",
    response_model=PipelineDetail,
    operation_id="admin_article_pipeline_retry",
)
def retry(article_id: uuid.UUID, body: RetryPipeline, session: DB, admin: Admin):
    article = request_processing_retry(
        session,
        article_id,
        target=body.target,
        expected_revision=body.expected_revision,
        expected_job_id=body.expected_job_id,
        actor=actor(admin),
    )
    session.commit()
    result = _detail(session, article)
    session.close()
    return result

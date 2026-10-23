"""Source lifecycle views and explicit, concurrency-checked processing requests.

Reading this board never creates work. Source review and source enablement remain
editorial decisions; the core lifecycle owns eligibility and task-specific retries.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from devfeed_core.json_types import JsonValue
from devfeed_core.models import Source, SourceCandidate, SourceReview, utcnow
from devfeed_core.read_models import ReadModelSnapshot
from devfeed_core.schemas import DatabaseText, InputModel, ORMModel
from devfeed_core.source_lifecycle import (
    SourceLifecycleFacts,
    advance_source_processing,
    retry_source_processing,
    source_readiness_batch,
)
from devfeed_core.source_types import SourceType
from devfeed_core.text_search import text_search
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import select

from devfeed_admin_api.auth import Admin, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/source-pipeline",
    tags=["admin-sources"],
    dependencies=[Depends(require_admin)],
)

SourcePipelineStage = Literal[
    "discovery",
    "profile",
    "assessment",
    "branding",
    "review",
    "ingestion",
    "active",
    "paused",
    "rejected",
]
SourcePipelineState = Literal["waiting", "running", "retry_wait", "blocked", "ready", "complete"]
SourcePipelineKind = Literal["source", "candidate"]
SourcePipelineTarget = Literal["discovery", "profile", "assessment", "branding", "ingestion"]
STAGES: tuple[SourcePipelineStage, ...] = (
    "discovery",
    "profile",
    "assessment",
    "branding",
    "review",
    "ingestion",
    "active",
    "paused",
    "rejected",
)
TERMINAL_STAGES = frozenset({"active", "paused", "rejected"})
ACTIVE_STATUSES = frozenset({"queued", "running"})
MAX_CURSOR = 2**31 - 1


class SourcePipelineRetryTarget(ORMModel):
    stage: SourcePipelineTarget
    job_id: uuid.UUID | None = None


class SourcePipelineCard(ORMModel):
    key: str
    kind: SourcePipelineKind
    id: uuid.UUID
    source_id: uuid.UUID | None
    candidate_id: uuid.UUID | None
    name: str
    website_url: str | None
    feed_url: str | None
    source_type: SourceType
    approval_status: Literal["pending", "approved", "rejected"]
    enabled: bool
    stage: SourcePipelineStage
    state: SourcePipelineState
    created_at: datetime
    updated_at: datetime
    snapshot: str
    can_advance: bool
    can_approve: bool
    can_ingest: bool
    next_action: str | None
    advance_label: str | None
    retry_targets: list[SourcePipelineRetryTarget]


class SourcePipelineColumn(ORMModel):
    stage: SourcePipelineStage
    total: int
    items: list[SourcePipelineCard]
    next_cursor: int | None


class SourcePipelineBoard(ORMModel):
    as_of: datetime
    terminal_since: datetime
    counts: dict[SourcePipelineStage, int]
    columns: list[SourcePipelineColumn]
    snapshot: ReadModelSnapshot | None = None


class SourcePipelineCheck(ORMModel):
    key: str
    status: str
    job_id: uuid.UUID | None = None
    error: str | None = None
    available_at: datetime | None = None


class SourcePipelineTask(ORMModel):
    stage: SourcePipelineTarget
    job_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    finished_at: datetime | None
    error: str | None


class SourcePipelineProfile(ORMModel):
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    language: str | None = None


class SourcePipelineReview(ORMModel):
    decision: str
    actor: str | None
    note: str | None
    created_at: datetime


class SourcePipelineDetail(ORMModel):
    item: SourcePipelineCard
    checks: list[SourcePipelineCheck]
    blockers: list[str]
    tasks: list[SourcePipelineTask]
    profile: SourcePipelineProfile
    assessment: dict[str, JsonValue] | None
    reviews: list[SourcePipelineReview]
    candidate_ids: list[uuid.UUID]


class SourcePipelineAdvance(InputModel):
    expected_snapshot: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]{64}$")


class SourcePipelineRetry(SourcePipelineAdvance):
    target: SourcePipelineTarget
    job_id: uuid.UUID | None = None


class SourcePipelineQuery:
    def __init__(
        self,
        q: DatabaseText = Query("", max_length=200),
        source_type: SourceType | None = None,
        needs_attention: bool = False,
        terminal_since: datetime | None = None,
        limit: int = Query(20, ge=1, le=50),
    ):
        now = utcnow()
        since = terminal_since or now - timedelta(days=7)
        if since.tzinfo is None:
            raise HTTPException(422, "terminal_since must include a timezone")
        since = since.astimezone(UTC)
        if since < now - timedelta(days=90) or since > now:
            raise HTTPException(422, "terminal_since must be within the last 90 days")
        self.q, self.source_type = q.strip(), source_type
        self.needs_attention, self.terminal_since, self.limit = needs_attention, since, limit


PipelineQuery = Annotated[SourcePipelineQuery, Depends()]


def _activity(facts: SourceLifecycleFacts) -> datetime:
    rows = ([facts.source] if facts.source is not None else []) + list(facts.candidates)
    values = [getattr(row, field, None) for row in rows for field in ("updated_at", "created_at")]
    values.extend(
        getattr(job, field, None)
        for job in facts.jobs.values()
        if job is not None
        for field in ("created_at", "finished_at")
    )
    return max(value for value in values if value is not None)


def _card(facts: SourceLifecycleFacts) -> SourcePipelineCard:
    source, candidate, readiness = facts.source, facts.candidate, facts.readiness
    subject = source if source is not None else candidate
    assert subject is not None
    kind: SourcePipelineKind = "source" if source is not None else "candidate"
    return SourcePipelineCard(
        key=facts.key,
        kind=kind,
        id=subject.id,
        source_id=source.id if source is not None else None,
        candidate_id=candidate.id if candidate is not None else None,
        name=subject.name,
        website_url=source.website_url if source is not None else candidate.identity_url,
        feed_url=source.feed_url if source is not None else None,
        source_type=source.source_type if source is not None else "publisher",
        approval_status=subject.approval_status,
        enabled=source.enabled if source is not None else False,
        stage=readiness.stage,
        state=readiness.state,
        created_at=subject.created_at,
        updated_at=_activity(facts),
        snapshot=facts.snapshot,
        can_advance=readiness.next_action is not None,
        can_approve=readiness.can_approve,
        can_ingest=readiness.can_ingest,
        next_action=readiness.next_action,
        advance_label=(
            {
                "resume": "Resume",
                "reopen": "Reopen for review",
                "discovery": "Find feed",
                "profile": "Look up profile",
                "assessment": "Assess source",
                "branding": "Process branding",
                "ingestion": "Fetch feed",
            }.get(readiness.next_action, "Run next step")
            if readiness.next_action is not None
            else None
        ),
        retry_targets=[
            SourcePipelineRetryTarget(
                stage=target,
                job_id=facts.jobs[target].id if facts.jobs.get(target) is not None else None,
            )
            for target in readiness.retry_targets
        ],
    )


def _facts(session, query: SourcePipelineQuery, now: datetime) -> Iterator[SourceLifecycleFacts]:
    sources = select(Source)
    candidates = select(SourceCandidate).where(SourceCandidate.source_id.is_(None))
    if query.q:
        sources = sources.where(
            text_search(query.q, Source.name, Source.website_url, Source.feed_url)
        )
        candidates = candidates.where(
            text_search(query.q, SourceCandidate.name, SourceCandidate.identity_url)
        )
    if query.source_type is not None:
        sources = sources.where(Source.source_type == query.source_type)
    # Classify the entire matching population, while retaining evidence for at
    # most one batch. Paging before classification would hide older running work.
    for kind, model, statement in (
        ("source", Source, sources),
        ("candidate", SourceCandidate, candidates),
    ):
        if kind == "candidate" and query.source_type == "aggregator":
            continue
        after = None
        while True:
            batch = statement.where(model.id > after) if after is not None else statement
            rows = list(session.scalars(batch.order_by(model.id).limit(200)))
            if not rows:
                break
            after = rows[-1].id
            facts = source_readiness_batch(
                session,
                rows if kind == "source" else [],
                rows if kind == "candidate" else [],
                now=now,
            )
            for item in facts.values():
                readiness = item.readiness
                active = any(
                    job is not None and job.status in ACTIVE_STATUSES for job in item.jobs.values()
                )
                if (
                    readiness.stage in TERMINAL_STAGES
                    and not active
                    and _activity(item) < query.terminal_since
                ):
                    continue
                if (
                    query.needs_attention
                    and readiness.state not in {"blocked", "retry_wait"}
                    and readiness.stage != "review"
                ):
                    continue
                yield item
            # GETs have no pending writes. Detach potentially large assessment
            # samples/results before loading the next batch.
            session.expunge_all()
            del facts, rows


def _sort_key(card: SourcePipelineCard) -> tuple[int, float, str]:
    activity = (
        -card.updated_at.timestamp()
        if card.stage in TERMINAL_STAGES
        else card.created_at.timestamp()
    )
    return (0 if card.state == "running" else 1, activity, card.key)


def _columns(
    session,
    query: SourcePipelineQuery,
    now: datetime,
    *,
    stage: SourcePipelineStage | None = None,
    cursor: int = 0,
) -> list[SourcePipelineColumn]:
    """Offline lifecycle reference; HTTP readers use indexed pipeline_reads."""
    stages = (stage,) if stage is not None else STAGES
    totals = {key: 0 for key in stages}
    buffers: dict[SourcePipelineStage, list[SourcePipelineCard]] = {key: [] for key in stages}
    keep = cursor + query.limit
    for facts in _facts(session, query, now):
        key = facts.readiness.stage
        if key not in totals:
            continue
        totals[key] += 1
        buffer = buffers[key]
        buffer.append(_card(facts))
        if len(buffer) > keep + 200:
            buffer.sort(key=_sort_key)
            del buffer[keep:]
    result = []
    for key in stages:
        items = sorted(buffers[key], key=_sort_key)[cursor:keep]
        result.append(
            SourcePipelineColumn(
                stage=key,
                total=totals[key],
                items=items,
                next_cursor=cursor + len(items) if cursor + len(items) < totals[key] else None,
            )
        )
    return result


def _detail(session, facts: SourceLifecycleFacts) -> SourcePipelineDetail:
    reviews = (
        list(
            session.scalars(
                select(SourceReview)
                .where(SourceReview.source_id == facts.source.id)
                .order_by(SourceReview.created_at.desc(), SourceReview.id)
                .limit(50)
            )
        )
        if facts.source is not None
        else []
    )
    return SourcePipelineDetail(
        item=_card(facts),
        checks=[
            SourcePipelineCheck(key=key, **jsonable_encoder(check))
            for key, check in facts.readiness.checks.items()
        ],
        blockers=facts.readiness.blockers,
        tasks=[
            SourcePipelineTask(
                stage=stage,
                job_id=job.id,
                **{
                    field: getattr(job, field)
                    for field in (
                        "status",
                        "attempts",
                        "created_at",
                        "available_at",
                        "finished_at",
                        "error",
                    )
                },
            )
            for stage, job in facts.jobs.items()
            if job is not None
        ],
        profile=SourcePipelineProfile.model_validate(facts.source)
        if facts.source is not None
        else SourcePipelineProfile(),
        assessment=jsonable_encoder(facts.source.relevance_assessment)
        if facts.source is not None and facts.source.relevance_assessment
        else None,
        reviews=[SourcePipelineReview.model_validate(review) for review in reviews],
        candidate_ids=[candidate.id for candidate in facts.candidates],
    )


@router.get("", response_model=SourcePipelineBoard, operation_id="admin_source_pipeline")
def board(session: DB, query: PipelineQuery):
    from devfeed_admin_api.pipeline_reads import serving_projection, source_columns

    now = utcnow()
    policy, snapshot = serving_projection(session, kind="source")
    columns = source_columns(session, query, now, policy=policy)
    result = SourcePipelineBoard(
        as_of=now,
        terminal_since=query.terminal_since,
        counts={column.stage: column.total for column in columns},
        columns=columns,
        snapshot=snapshot,
    )
    session.close()
    return result


@router.get(
    "/columns/{stage}",
    response_model=SourcePipelineColumn,
    operation_id="admin_source_pipeline_column",
)
def column(
    stage: SourcePipelineStage,
    session: DB,
    query: PipelineQuery,
    cursor: int = Query(0, ge=0, le=MAX_CURSOR),
):
    from devfeed_admin_api.pipeline_reads import source_columns

    result = source_columns(session, query, utcnow(), stage=stage, cursor=cursor)[0]
    session.close()
    return result


@router.get(
    "/items/{kind}/{identifier}",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_detail",
)
def detail(kind: SourcePipelineKind, identifier: uuid.UUID, session: DB):
    from devfeed_core.services import RecordNotFound

    if kind == "candidate":
        candidate = session.get(SourceCandidate, identifier)
        if candidate is None:
            raise RecordNotFound("Source pipeline item not found; reload the board")
        if candidate.source_id is None:
            facts = source_readiness_batch(session, [], [candidate])
            result = _detail(session, facts[f"candidate:{identifier}"])
            session.close()
            return result
        identifier = candidate.source_id
    source = session.get(Source, identifier)
    if source is None:
        raise RecordNotFound("Source pipeline item not found; reload the board")
    facts = source_readiness_batch(session, [source])
    result = _detail(session, facts[f"source:{identifier}"])
    session.close()
    return result


@router.post(
    "/items/{kind}/{identifier}/advance",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_advance",
)
def advance(
    kind: SourcePipelineKind,
    identifier: uuid.UUID,
    body: SourcePipelineAdvance,
    session: DB,
    admin: Admin,
):
    facts = advance_source_processing(
        session, kind, identifier, body.expected_snapshot, actor=admin.subject
    )
    result = _detail(session, facts)
    session.commit()
    session.close()
    return result


@router.post(
    "/items/{kind}/{identifier}/retry",
    response_model=SourcePipelineDetail,
    operation_id="admin_source_pipeline_retry",
)
def retry(
    kind: SourcePipelineKind,
    identifier: uuid.UUID,
    body: SourcePipelineRetry,
    session: DB,
    admin: Admin,
):
    facts = retry_source_processing(
        session,
        kind,
        identifier,
        body.expected_snapshot,
        body.target,
        body.job_id,
        actor=admin.subject,
    )
    result = _detail(session, facts)
    session.commit()
    session.close()
    return result

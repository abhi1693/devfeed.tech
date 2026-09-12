"""Run records and guarded retries, grouped by pipeline."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from devfeed_core.job_definitions import JOB_DEFINITIONS
from devfeed_core.job_logs import JobKind, JobLogPage, read_job_logs, validate_cursor
from devfeed_core.job_retries import retry_candidate, retry_failed_job
from devfeed_core.json_types import JsonValue
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    NotificationDelivery,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
)
from devfeed_core.schemas import ORMModel
from devfeed_http.schemas import ErrorResponse
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import String, Uuid, case, cast, false, func, literal, null, select, union_all
from sqlalchemy.orm import load_only

from devfeed_admin_api.auth import Admin, actor, require_admin
from devfeed_admin_api.dependencies import DB, get_redis
from devfeed_admin_api.job_views import JOB_FIELDS
from devfeed_admin_api.pagination import Listing, Page, paginate, record
from devfeed_admin_api.search import text_search

MODELS = {kind: d.model for kind, d in JOB_DEFINITIONS.items() if d.admin_visible}
router = APIRouter(
    prefix="/v1/admin/jobs", tags=["admin-jobs"], dependencies=[Depends(require_admin)]
)


def retry_candidates(model):
    """Batch eligibility for filtered/sorted lists, without a history lookup per row.

    A failed job is eligible only when it is newest and its subject has no active
    run, even if that active run has an older timestamp. Single-job operations keep
    using the indexed correlated predicate in core.job_retries.
    """
    if model is NotificationDelivery:
        return select(model.id).where(model.status == "failed")
    subject = (
        (model.proposal_id, model.topic_id)
        if model is TopicAnalysisJob
        else (model.article_id if hasattr(model, "article_id") else model.source_id,)
    )
    history = select(
        model.id,
        model.status,
        func.row_number()
        .over(partition_by=subject, order_by=(model.created_at.desc(), model.id.desc()))
        .label("position"),
        func.bool_or(model.status.in_(["queued", "running"]))
        .over(partition_by=subject)
        .label("active"),
    ).subquery()
    return select(history.c.id).where(
        history.c.status == "failed", history.c.position == 1, ~history.c.active
    )


class AdminJobOut(ORMModel):
    id: uuid.UUID
    kind: JobKind
    status: str
    retryable: bool = False
    attempts: int
    created_at: datetime
    available_at: datetime
    finished_at: datetime | None
    source_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    article_id: uuid.UUID | None = None
    target_name: str | None = None
    error: str | None
    details: dict[str, JsonValue]


def job_view(job, kind, *, retryable=None) -> AdminJobOut:
    values = {field: getattr(job, field) for field in JOB_FIELDS[kind]}
    if job.status == "failed" and retryable is False:
        values["status"] = "retried"
    return AdminJobOut(
        **{field: values[field] for field in AdminJobOut.model_fields if field in values},
        kind=kind,
        retryable=bool(retryable),
        # Presentation metadata includes ORM timestamps such as dispatched_at.
        # Normalize them to their wire representation before JSON-value validation.
        details=jsonable_encoder(
            {
                field: value
                for field, value in values.items()
                if field not in AdminJobOut.model_fields
            }
        ),
    )


@router.get(
    "/ai-analysis", response_model=Page[AdminJobOut], operation_id="admin_ai_analysis_jobs_list"
)
def ai_analysis_jobs(
    session: DB,
    query: Listing,
    status: Literal["queued", "running", "succeeded", "failed", "retried"] | None = None,
    analysis_type: Literal["articles", "topics"] | None = None,
    article_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
    retryable_only: bool = False,
):
    """Page article and topic research runs together before loading their details."""
    order = query.sort or "-created_at"
    # Retry eligibility is correlated history work. Ordinary chronological pages
    # need it only for the returned IDs, not for every row in both job tables.
    needs_retry = retryable_only or status in {"failed", "retried"} or order.lstrip("-") == "status"
    batch_retry = status == "retried" or (status is None and order.removeprefix("-") == "status")
    eligibility = {
        model: (model.id.in_(retry_candidates(model)) if batch_retry else retry_candidate(model))
        for model in (ArticleAnalysisJob, TopicAnalysisJob)
    }
    runs = union_all(
        select(
            ArticleAnalysisJob.id,
            literal("analysis").label("kind"),
            ArticleAnalysisJob.status,
            ArticleAnalysisJob.created_at,
            (eligibility[ArticleAnalysisJob] if needs_retry else false()).label("retryable"),
            Article.title.label("target_name"),
            ArticleAnalysisJob.article_id,
            cast(null(), Uuid).label("proposal_id"),
            cast(null(), Uuid).label("topic_id"),
        ).join(Article, Article.id == ArticleAnalysisJob.article_id),
        select(
            TopicAnalysisJob.id,
            literal("topic-analysis").label("kind"),
            TopicAnalysisJob.status,
            TopicAnalysisJob.created_at,
            (eligibility[TopicAnalysisJob] if needs_retry else false()).label("retryable"),
            func.coalesce(TopicProposal.proposed["name"].astext, Topic.name).label("target_name"),
            cast(null(), Uuid).label("article_id"),
            TopicAnalysisJob.proposal_id,
            TopicAnalysisJob.topic_id,
        )
        .outerjoin(TopicProposal, TopicProposal.id == TopicAnalysisJob.proposal_id)
        .outerjoin(Topic, Topic.id == TopicAnalysisJob.topic_id),
    ).subquery()
    statement = select(runs)
    if retryable_only and status not in {None, "failed"}:
        statement = statement.where(false())
    elif retryable_only or status == "failed":
        statement = statement.where(runs.c.status == "failed", runs.c.retryable)
    elif status == "retried":
        statement = statement.where(runs.c.status == "failed", ~runs.c.retryable)
    elif status:
        statement = statement.where(runs.c.status == status)
    if analysis_type:
        statement = statement.where(
            runs.c.kind == ("analysis" if analysis_type == "articles" else "topic-analysis")
        )
    if article_id:
        statement = statement.where(runs.c.article_id == article_id)
    if proposal_id:
        statement = statement.where(runs.c.proposal_id == proposal_id)
    if topic_id:
        statement = statement.where(runs.c.topic_id == topic_id)
    if query.q:
        statement = statement.where(
            text_search(query.q, cast(runs.c.id, String), runs.c.target_name)
        )
    display_status = case(
        ((runs.c.status == "failed") & ~runs.c.retryable, "retried"), else_=runs.c.status
    )
    column = {"created_at": runs.c.created_at, "status": display_status}.get(
        order.removeprefix("-")
    )
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(
        select(func.count()).select_from(
            statement.with_only_columns(runs.c.id, runs.c.kind).subquery()
        )
    )
    page = (
        session.execute(
            statement.order_by(
                column.desc() if order.startswith("-") else column.asc(), runs.c.id, runs.c.kind
            )
            .offset(query.offset)
            .limit(query.limit)
        )
        .mappings()
        .all()
    )
    records = {}
    for kind in ("analysis", "topic-analysis"):
        identifiers = [row["id"] for row in page if row["kind"] == kind]
        if identifiers:
            model = MODELS[kind]
            for job, eligible in session.execute(
                select(model, retry_candidate(model) if not needs_retry else false())
                .where(model.id.in_(identifiers))
                .options(
                    load_only(
                        *(getattr(model, field) for field in JOB_FIELDS[kind]), raiseload=True
                    )
                )
            ):
                value = job_view(job, kind, retryable=eligible)
                records[kind, value.id] = value
    return {
        "items": [
            records[row["kind"], row["id"]].model_copy(
                update={
                    "target_name": row["target_name"],
                    **(
                        {
                            "retryable": row["retryable"],
                            "status": "retried"
                            if row["status"] == "failed" and not row["retryable"]
                            else row["status"],
                        }
                        if needs_retry
                        else {}
                    ),
                }
            )
            for row in page
            if (row["kind"], row["id"]) in records
        ],
        "total": total,
        "offset": query.offset,
        "limit": query.limit,
    }


@router.get("/{kind}", response_model=Page[AdminJobOut], operation_id="admin_jobs_list")
def jobs(
    kind: JobKind,
    session: DB,
    query: Listing,
    status: Literal["queued", "running", "succeeded", "failed", "retried"] | None = None,
    source_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
    retryable_only: bool = False,
):
    model = MODELS[kind]
    statement = select(model).options(
        load_only(*(getattr(model, field) for field in JOB_FIELDS[kind]), raiseload=True)
    )
    # Batch broad status ordering/history views; selective unresolved-failure
    # filters use the indexed predicate. Add eligibility once when both filters
    # are supplied, avoiding duplicate semi-joins to the same history.
    batch_retry = status == "retried" or (status is None and query.sort in {"status", "-status"})
    eligible = model.id.in_(retry_candidates(model)) if batch_retry else retry_candidate(model)
    if retryable_only and status not in {None, "failed"}:
        statement = statement.where(false())
    elif retryable_only or status == "failed":
        statement = statement.where(model.status == "failed", eligible)
    elif status == "retried":
        statement = statement.where(model.status == "failed", ~eligible)
    elif status:
        statement = statement.where(model.status == status)
    if query.q:
        statement = statement.where(text_search(query.q, cast(model.id, String)))
    for name, value in (("source_id", source_id), ("article_id", article_id)):
        if value:
            column = getattr(model, name, None)
            statement = (
                statement.where(column == value) if column is not None else statement.where(false())
            )
    result = paginate(
        session,
        statement,
        query,
        {
            "created_at": model.created_at,
            "status": case(((model.status == "failed") & ~eligible, "retried"), else_=model.status),
        },
        "-created_at",
    )
    # Resolve labels alongside retry eligibility for this page: one query,
    # rather than one full source/article request per row in the browser.
    subject = (
        Source if hasattr(model, "source_id") else Article if hasattr(model, "article_id") else None
    )
    metadata = select(model.id, retry_candidate(model))
    if subject is not None:
        field = "source_id" if subject is Source else "article_id"
        label = Source.name if subject is Source else Article.title
        metadata = metadata.add_columns(label).outerjoin(
            subject, subject.id == getattr(model, field)
        )
    else:
        metadata = metadata.add_columns(null())
    rows = (
        session.execute(metadata.where(model.id.in_([item.id for item in result["items"]]))).all()
        if result["items"]
        else []
    )
    retryable_ids = {identifier for identifier, eligible, _ in rows if eligible}
    names = {identifier: name for identifier, _, name in rows}
    result["items"] = [
        job_view(item, kind, retryable=item.id in retryable_ids).model_copy(
            update={"target_name": names.get(item.id)}
        )
        for item in result["items"]
    ]
    # The result is detached presentation data; free the connection before
    # FastAPI queues response validation on its shared worker threads.
    session.close()
    return result


@router.get("/{kind}/{job_id}", response_model=AdminJobOut, operation_id="admin_job_get")
def detail(kind: JobKind, job_id: uuid.UUID, session: DB):
    model = MODELS[kind]
    job = record(session, model, job_id)
    retryable = bool(
        session.scalar(select(model.id).where(model.id == job_id, retry_candidate(model)))
    )
    return job_view(job, kind, retryable=retryable)


@router.post("/{kind}/{job_id}/retry", response_model=AdminJobOut, operation_id="admin_job_retry")
def retry(kind: JobKind, job_id: uuid.UUID, admin: Admin, session: DB):
    previous = record(session, MODELS[kind], job_id)
    job = retry_failed_job(session, previous, kind, actor(admin))
    session.commit()
    return job_view(job, kind)


class AdminJobLogs(JobLogPage):
    job_status: str
    attempts: int


@router.get(
    "/{kind}/{job_id}/logs",
    response_model=AdminJobLogs,
    operation_id="admin_job_logs",
    responses={
        404: {"model": ErrorResponse, "description": "Job not found"},
        503: {"model": ErrorResponse, "description": "Log storage unavailable"},
    },
)
def runtime_logs(
    kind: JobKind,
    job_id: uuid.UUID,
    session: DB,
    redis: Annotated[Redis, Depends(get_redis)],
    after: Annotated[str | None, Query(max_length=41)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    if after is not None:
        try:
            validate_cursor(after)
        except ValueError:
            raise HTTPException(422, "Invalid log cursor") from None
    job = record(session, MODELS[kind], job_id)
    status, attempts = job.status, job.attempts
    # No connection/transaction stays open while waiting on Redis.
    session.close()
    try:
        page = read_job_logs(redis, kind, job_id, after=after, limit=limit)
    except (RedisError, OSError, ValueError):
        raise HTTPException(503, "Job logs are temporarily unavailable. Please retry.") from None
    return AdminJobLogs(**page.model_dump(), job_status=status, attempts=attempts)

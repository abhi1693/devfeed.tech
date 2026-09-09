"""Read-only run records, grouped by pipeline. These are not editable content."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from devfeed_core.job_logs import JobKind, JobLogPage, read_job_logs, validate_cursor
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    NotificationDelivery,
    SourceEnrichmentJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
)
from devfeed_core.schemas import ORMModel
from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import String, Uuid, cast, false, func, literal, null, or_, select, union_all

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB, get_redis
from devfeed_admin_api.pagination import Listing, Page, paginate, record

JobModel = (
    type[IngestionJob]
    | type[ArticleEnrichmentJob]
    | type[ArticleImageJob]
    | type[SourceEnrichmentJob]
    | type[ArticleAnalysisJob]
    | type[TopicAnalysisJob]
    | type[NotificationDelivery]
)
MODELS: dict[str, JobModel] = {
    "ingestion": IngestionJob,
    "article-enrichment": ArticleEnrichmentJob,
    "images": ArticleImageJob,
    "source-enrichment": SourceEnrichmentJob,
    "analysis": ArticleAnalysisJob,
    "topic-analysis": TopicAnalysisJob,
    "notifications": NotificationDelivery,
}
router = APIRouter(
    prefix="/v1/admin/jobs", tags=["admin-jobs"], dependencies=[Depends(require_admin)]
)


class AdminJobOut(ORMModel):
    id: uuid.UUID
    kind: JobKind
    status: str
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
    details: dict


def job_view(job, kind) -> AdminJobOut:
    excluded = {"lease_token", "lease_until", "input_snapshot", "catalog_snapshot"}
    values = {
        column.name: getattr(job, column.name)
        for column in job.__table__.columns
        if column.name not in excluded
    }
    return AdminJobOut(
        **{field: values[field] for field in AdminJobOut.model_fields if field in values},
        kind=kind,
        details={
            field: value for field, value in values.items() if field not in AdminJobOut.model_fields
        },
    )


@router.get(
    "/ai-analysis", response_model=Page[AdminJobOut], operation_id="admin_ai_analysis_jobs_list"
)
def ai_analysis_jobs(
    session: DB,
    query: Listing,
    status: Literal["queued", "running", "succeeded", "failed"] | None = None,
    analysis_type: Literal["articles", "topics"] | None = None,
    article_id: uuid.UUID | None = None,
    proposal_id: uuid.UUID | None = None,
    topic_id: uuid.UUID | None = None,
):
    """Page article and topic research runs together before loading their details."""
    runs = union_all(
        select(
            ArticleAnalysisJob.id,
            literal("analysis").label("kind"),
            ArticleAnalysisJob.status,
            ArticleAnalysisJob.created_at,
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
            func.coalesce(TopicProposal.proposed["name"].astext, Topic.name).label("target_name"),
            cast(null(), Uuid).label("article_id"),
            TopicAnalysisJob.proposal_id,
            TopicAnalysisJob.topic_id,
        )
        .outerjoin(TopicProposal, TopicProposal.id == TopicAnalysisJob.proposal_id)
        .outerjoin(Topic, Topic.id == TopicAnalysisJob.topic_id),
    ).subquery()
    statement = select(runs)
    if status:
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
            or_(
                cast(runs.c.id, String).icontains(query.q, autoescape=True),
                runs.c.target_name.icontains(query.q, autoescape=True),
            )
        )
    order = query.sort or "-created_at"
    column = {"created_at": runs.c.created_at, "status": runs.c.status}.get(order.removeprefix("-"))
    if column is None:
        raise HTTPException(422, "Unsupported sort field")
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
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
            for job in session.scalars(select(model).where(model.id.in_(identifiers))):
                value = job_view(job, kind)
                records[kind, value.id] = value
    return {
        "items": [
            records[row["kind"], row["id"]].model_copy(update={"target_name": row["target_name"]})
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
    status: Literal["queued", "running", "succeeded", "failed"] | None = None,
    source_id: uuid.UUID | None = None,
    article_id: uuid.UUID | None = None,
):
    model = MODELS[kind]
    statement = select(model)
    if status:
        statement = statement.where(model.status == status)
    if query.q:
        statement = statement.where(cast(model.id, String).icontains(query.q, autoescape=True))
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
        {"created_at": model.created_at, "status": model.status},
        "-created_at",
    )
    result["items"] = [job_view(item, kind) for item in result["items"]]
    return result


@router.get("/{kind}/{job_id}", response_model=AdminJobOut, operation_id="admin_job_get")
def detail(kind: JobKind, job_id: uuid.UUID, session: DB):
    return job_view(record(session, MODELS[kind], job_id), kind)


class AdminJobLogs(JobLogPage):
    job_status: str
    attempts: int


@router.get(
    "/{kind}/{job_id}/logs",
    response_model=AdminJobLogs,
    operation_id="admin_job_logs",
    responses={
        404: {"description": "Job not found"},
        503: {"description": "Log storage unavailable"},
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

"""Read-only run records, grouped by pipeline. These are not editable content."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from devfeed_core.job_logs import JobKind, JobLogPage, read_job_logs, validate_cursor
from devfeed_core.models import (
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    NotificationDelivery,
    SourceEnrichmentJob,
)
from devfeed_core.schemas import ORMModel
from fastapi import APIRouter, Depends, HTTPException, Query
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import String, cast, false, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB, get_redis
from devfeed_admin_api.pagination import Listing, Page, paginate, record

JobModel = (
    type[IngestionJob]
    | type[ArticleEnrichmentJob]
    | type[ArticleImageJob]
    | type[SourceEnrichmentJob]
    | type[ArticleAnalysisJob]
    | type[NotificationDelivery]
)
MODELS: dict[str, JobModel] = {
    "ingestion": IngestionJob,
    "article-enrichment": ArticleEnrichmentJob,
    "images": ArticleImageJob,
    "source-enrichment": SourceEnrichmentJob,
    "analysis": ArticleAnalysisJob,
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
    article_id: uuid.UUID | None = None
    error: str | None
    details: dict


def job_view(job, kind):
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

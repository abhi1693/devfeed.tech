import logging
import uuid
from datetime import datetime
from typing import Literal, cast

from devfeed_core.models import (
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    SourceEnrichmentJob,
)
from devfeed_core.schemas import ArticleEnrichmentJobOut, JobOut
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import func, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/admin/ingestion", tags=["admin-ingestion"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


@router.get("/article-jobs", response_model=list[ArticleEnrichmentJobOut])
def article_jobs(
    session: DB,
    article_id: uuid.UUID | None = None,
    status: Literal["queued", "running", "succeeded", "failed"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    statement = select(ArticleEnrichmentJob)
    if article_id:
        statement = statement.where(ArticleEnrichmentJob.article_id == article_id)
    if status:
        statement = statement.where(ArticleEnrichmentJob.status == status)
    return session.scalars(
        statement.order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/article-jobs/{job_id}", response_model=ArticleEnrichmentJobOut)
def article_job_detail(job_id: uuid.UUID, session: DB):
    job = session.get(ArticleEnrichmentJob, job_id)
    if job is None:
        raise HTTPException(404, "Article enrichment job not found")
    return job


@router.get("/jobs", response_model=list[JobOut])
def jobs(
    session: DB,
    source_id: uuid.UUID | None = None,
    status: Literal["queued", "running", "succeeded", "failed"] | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    statement = select(IngestionJob)
    if source_id:
        statement = statement.where(IngestionJob.source_id == source_id)
    if status:
        statement = statement.where(IngestionJob.status == status)
    return session.scalars(
        statement.order_by(IngestionJob.created_at.desc(), IngestionJob.id)
        .offset(offset)
        .limit(limit)
    ).all()


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_detail(job_id: uuid.UUID, session: DB):
    job = session.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


class IngestionStatus(BaseModel):
    jobs: dict[str, int]
    image_jobs: dict[str, int]
    source_enrichment_jobs: dict[str, int]
    article_enrichment_jobs: dict[str, int]
    oldest_active_image_job_at: datetime | None
    oldest_active_job_at: datetime | None
    scheduler_last_seen: str | None
    redis_available: bool
    queue_depth: int | None


@router.get("/status", response_model=IngestionStatus)
def status(session: DB):
    counts = {
        state: count
        for state, count in session.execute(
            select(IngestionJob.status, func.count()).group_by(IngestionJob.status)
        )
    }
    oldest = session.scalar(
        select(func.min(IngestionJob.created_at)).where(
            IngestionJob.status.in_(["queued", "running"])
        )
    )
    image_counts = {
        state: count
        for state, count in session.execute(
            select(ArticleImageJob.status, func.count()).group_by(ArticleImageJob.status)
        )
    }
    oldest_image = session.scalar(
        select(func.min(ArticleImageJob.created_at)).where(
            ArticleImageJob.status.in_(["queued", "running"])
        )
    )
    profile_counts = {
        state: count
        for state, count in session.execute(
            select(SourceEnrichmentJob.status, func.count()).group_by(SourceEnrichmentJob.status)
        )
    }
    try:
        heartbeat = cast(bytes | None, get_redis().get("devfeed:scheduler:heartbeat"))
        depth = get_redis().llen("rq:queue:ingestion")
        redis_available = True
    except RedisError as exc:
        logger.warning("ingestion_status_unavailable", extra={"error_type": type(exc).__name__})
        heartbeat, depth, redis_available = None, None, False
    return {
        "jobs": counts,
        "image_jobs": image_counts,
        "source_enrichment_jobs": profile_counts,
        "article_enrichment_jobs": {
            state: count
            for state, count in session.execute(
                select(ArticleEnrichmentJob.status, func.count()).group_by(
                    ArticleEnrichmentJob.status
                )
            )
        },
        "oldest_active_image_job_at": oldest_image,
        "oldest_active_job_at": oldest,
        "scheduler_last_seen": heartbeat.decode() if heartbeat else None,
        "redis_available": redis_available,
        "queue_depth": depth,
    }

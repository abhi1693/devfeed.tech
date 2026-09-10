"""Read-only worker snapshots. RQ transport health and durable job outcomes are distinct."""

import json
import uuid
import zlib
from datetime import UTC, datetime
from typing import Annotated, Literal

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    NotificationDelivery,
    ResearchVerificationJob,
    Source,
    SourceEnrichmentJob,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
)
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/admin/workers", tags=["admin-workers"], dependencies=[Depends(require_admin)]
)
QUEUES = ("ingestion", "analysis", "relationships", "notifications")
FUNCTIONS = {
    "devfeed_aggregator.tasks.ingest": ("ingestion", IngestionJob),
    "devfeed_aggregator.article_tasks.enrich_article": ("article-enrichment", ArticleEnrichmentJob),
    "devfeed_aggregator.image_tasks.enrich_image": ("images", ArticleImageJob),
    "devfeed_aggregator.source_tasks.enrich_source": ("source-enrichment", SourceEnrichmentJob),
    "devfeed_aggregator.analysis_tasks.analyze_article": ("analysis", ArticleAnalysisJob),
    "devfeed_aggregator.topic_analysis_tasks.analyze_topic": ("topic-analysis", TopicAnalysisJob),
    "devfeed_aggregator.research_verification_tasks.verify_research": (
        "research-verification",
        ResearchVerificationJob,
    ),
    "devfeed_notifications.delivery.deliver_notification": ("notifications", NotificationDelivery),
}
WORKER_FIELDS = (
    "queues",
    "state",
    "birth",
    "last_heartbeat",
    "hostname",
    "pid",
    "current_job",
    "successful_job_count",
    "failed_job_count",
    "total_working_time",
    "death",
)


class WorkerJob(BaseModel):
    rq_id: str
    id: uuid.UUID | None = None
    kind: str | None = None
    started_at: datetime | None = None
    elapsed_seconds: int | None = None
    status: str | None = None
    outcome: str | None = None
    target_name: str | None = None
    article_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None


class WorkerOut(BaseModel):
    name: str
    role: Literal["ai", "background", "mixed"]
    queues: list[str]
    state: str
    registered: bool
    hostname: str | None
    pid: int | None
    started_at: datetime | None
    last_heartbeat: datetime | None
    uptime_seconds: int | None
    heartbeat_age_seconds: int | None
    registration_ttl_seconds: int
    completed_executions: int
    failed_executions: int
    working_seconds: float
    current_job: WorkerJob | None = None


class QueueOut(BaseModel):
    name: str
    registered_workers: int
    busy_workers: int
    idle_workers: int
    suspended_workers: int
    dispatched: int
    queued: int = 0
    running: int = 0
    failed: int = 0
    succeeded: int = 0
    oldest_queued_at: datetime | None = None
    review_required: int = 0


class WorkersSnapshot(BaseModel):
    generated_at: datetime
    workers: list[WorkerOut]
    queues: list[QueueOut]
    ai_cooldown_seconds: int


def decoded(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(decoded(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def age(value, now):
    return max(0, int((now - value).total_seconds())) if value else None


def number(value, default=0):
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return default


def job_identity(data):
    """Read only the allowlisted function and UUID from RQ's JSON serializer, never pickle."""
    if not data or len(data) > 16384:
        return None
    try:
        try:
            inflater = zlib.decompressobj()
            raw = inflater.decompress(data, 16385)
            if len(raw) > 16384 or not inflater.eof:
                return None
        except zlib.error:
            raw = data
        function, instance, args, kwargs = json.loads(raw)
        if not isinstance(function, str) or function not in FUNCTIONS or instance is not None:
            return None
        if not isinstance(args, list) or len(args) != 1 or not isinstance(args[0], str):
            return None
        return FUNCTIONS[function], uuid.UUID(args[0])
    except (ValueError, TypeError, UnicodeError):
        return None


def current_job(connection, session, identifier, now):
    data, started = connection.hmget("rq:job:" + identifier, "data", "started_at")
    started_at = timestamp(started)
    result = WorkerJob(
        rq_id=identifier, started_at=started_at, elapsed_seconds=age(started_at, now)
    )
    identity = job_identity(data)
    if identity is None:
        return result
    (kind, model), job_id = identity
    job = session.get(model, job_id)
    if job is None:
        return result  # The worker may have just completed or the record was deleted.
    result.id, result.kind, result.status = job_id, kind, job.status
    result.outcome = getattr(job, "outcome", None)
    target_job = session.get(TopicAnalysisJob, job_id) if kind == "research-verification" else job
    for field, target, label in (
        ("article_id", Article, "title"),
        ("source_id", Source, "name"),
        ("topic_id", Topic, "name"),
        ("proposal_id", TopicProposal, None),
    ):
        target_id = getattr(target_job, field, None)
        if target_id:
            setattr(result, field, target_id)
            row = session.get(target, target_id)
            if row is not None and result.target_name is None:
                result.target_name = getattr(row, label) if label else row.proposed.get("name")
    return result


def read_workers(connection, session, now, name=None):
    # SMEMBERS is bounded by the worker population, never by the job backlog.
    keys = (
        ["rq:worker:" + name]
        if name is not None
        else sorted(decoded(key) for key in connection.smembers("rq:workers"))
    )
    if len(keys) > 1000:
        raise HTTPException(503, "Worker registry exceeds the snapshot limit")
    with connection.pipeline(transaction=False) as pipe:
        for key in keys:
            pipe.hmget(key, *WORKER_FIELDS)
            pipe.ttl(key)
        values = pipe.execute()
    workers = []
    for index, key in enumerate(keys):
        raw, ttl = values[index * 2 : index * 2 + 2]
        fields = dict(zip(WORKER_FIELDS, map(decoded, raw), strict=True))
        if not any(raw):
            continue  # Expired membership is normal; reads must not clean RQ registries.
        queues = [q for q in (fields["queues"] or "").split(",") if q in QUEUES]
        if not queues:
            continue
        registered = ttl > 0 and not fields["death"]
        started, heartbeat = timestamp(fields["birth"]), timestamp(fields["last_heartbeat"])
        ai = any(q in ("analysis", "relationships") for q in queues)
        background = any(q in ("ingestion", "notifications") for q in queues)
        worker = WorkerOut(
            name=key.removeprefix("rq:worker:"),
            queues=queues,
            role="mixed" if ai and background else "ai" if ai else "background",
            state=(fields["state"] or "unknown") if registered else "offline",
            registered=registered,
            hostname=fields["hostname"],
            pid=number(fields["pid"]) or None,
            started_at=started,
            last_heartbeat=heartbeat,
            uptime_seconds=age(started, now),
            heartbeat_age_seconds=age(heartbeat, now),
            registration_ttl_seconds=ttl,
            completed_executions=number(fields["successful_job_count"]),
            failed_executions=number(fields["failed_job_count"]),
            working_seconds=number(
                fields["total_working_time"] and fields["total_working_time"].split(".")[0]
            ),
        )
        if registered and fields["current_job"]:
            worker.current_job = current_job(connection, session, fields["current_job"], now)
        workers.append(worker)
    return workers


def queue_snapshot(connection, session, workers):
    with connection.pipeline(transaction=False) as pipe:
        for name in QUEUES:
            pipe.llen("rq:queue:" + name)
        depths = pipe.execute()
    queues = {}
    for name, depth in zip(QUEUES, depths, strict=True):
        assigned = [w for w in workers if w.registered and name in w.queues]
        queues[name] = QueueOut(
            name=name,
            dispatched=depth,
            registered_workers=len(assigned),
            busy_workers=sum(w.state == "busy" for w in assigned),
            idle_workers=sum(w.state == "idle" for w in assigned),
            suspended_workers=sum(w.state == "suspended" for w in assigned),
        )
    # Durable work includes jobs not dispatched yet and scheduled retries.
    for model, name, condition in (
        (IngestionJob, "ingestion", None),
        (ArticleEnrichmentJob, "ingestion", None),
        (ArticleImageJob, "ingestion", None),
        (SourceEnrichmentJob, "ingestion", None),
        (ArticleAnalysisJob, "analysis", None),
        (TopicAnalysisJob, "analysis", TopicAnalysisJob.topic_id.is_(None)),
        (TopicAnalysisJob, "relationships", TopicAnalysisJob.topic_id.is_not(None)),
        (ResearchVerificationJob, "analysis", ResearchVerificationJob.relationships.is_(False)),
        (ResearchVerificationJob, "relationships", ResearchVerificationJob.relationships.is_(True)),
        (NotificationDelivery, "notifications", None),
    ):
        statement = select(model.status, func.count(), func.min(model.created_at)).group_by(
            model.status
        )
        if condition is not None:
            statement = statement.where(condition)
        queue = queues[name]
        for status, count, earliest in session.execute(statement):
            if status in {"queued", "running", "failed", "succeeded"}:
                setattr(queue, status, getattr(queue, status) + count)
            if status == "queued" and earliest:
                queue.oldest_queued_at = min(queue.oldest_queued_at or earliest, earliest)
    for relationships, count in session.execute(
        select(ResearchVerificationJob.relationships, func.count())
        .where(ResearchVerificationJob.outcome == "review_required")
        .group_by(ResearchVerificationJob.relationships)
    ):
        queues["relationships" if relationships else "analysis"].review_required = count
    return list(queues.values())


@router.get("", response_model=WorkersSnapshot, operation_id="admin_workers_snapshot")
def snapshot(response: Response, session: DB, connection: Annotated[Redis, Depends(get_redis)]):
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    try:
        workers = read_workers(connection, session, now)
        return WorkersSnapshot(
            generated_at=now,
            workers=workers,
            queues=queue_snapshot(connection, session, workers),
            ai_cooldown_seconds=cooldown_remaining(connection),
        )
    except RedisError:
        raise HTTPException(503, "Worker telemetry is temporarily unavailable") from None


@router.get("/{name}", response_model=WorkerOut, operation_id="admin_worker_get")
def detail(
    response: Response,
    session: DB,
    connection: Annotated[Redis, Depends(get_redis)],
    name: Annotated[str, Path(min_length=1, max_length=256, pattern=r"^[a-zA-Z0-9_.:-]+$")],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        workers = read_workers(connection, session, datetime.now(UTC), name)
    except RedisError:
        raise HTTPException(503, "Worker telemetry is temporarily unavailable") from None
    if not workers:
        raise HTTPException(404, "Worker is no longer registered; it may have stopped or restarted")
    return workers[0]

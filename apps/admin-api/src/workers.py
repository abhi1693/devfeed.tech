"""Read-only worker snapshots. RQ transport health and durable job outcomes are distinct."""

import json
import uuid
import zlib
from datetime import UTC, datetime
from typing import Annotated, Literal

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.job_definitions import JOB_DEFINITIONS, Job, queue_lanes
from devfeed_core.models import (
    Article,
    ResearchVerificationJob,
    Source,
    Topic,
    TopicAnalysisJob,
    TopicProposal,
)
from devfeed_core.worker_queues import AI_QUEUES, BACKGROUND_QUEUES, QUEUES
from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import lazyload, load_only

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/admin/workers", tags=["admin-workers"], dependencies=[Depends(require_admin)]
)
FUNCTIONS = {d.handler: (d.kind, d.model) for d in JOB_DEFINITIONS.values()}
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
    role: Literal["ai", "background", "mixed", "solver"]
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


def _load_rows(session, model, identifiers, fields):
    if not identifiers:
        return {}
    columns = [getattr(model, field) for field in fields if hasattr(model, field)]
    return {
        row.id: row
        for row in session.scalars(
            select(model)
            .options(load_only(model.id, *columns), lazyload("*"))
            .where(model.id.in_(identifiers))
        )
    }


def current_jobs(connection, session, identifiers, now) -> dict[str, WorkerJob]:
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers:
        return {}
    with connection.pipeline(transaction=False) as pipe:
        for identifier in identifiers:
            pipe.hmget("rq:job:" + identifier, "data", "started_at")
        values = pipe.execute()
    results, identities = {}, {}
    grouped: dict[type[Job], set[uuid.UUID]] = {}
    for identifier, (data, started) in zip(identifiers, values, strict=True):
        started_at = timestamp(started)
        results[identifier] = WorkerJob(
            rq_id=identifier, started_at=started_at, elapsed_seconds=age(started_at, now)
        )
        if identity := job_identity(data):
            identities[identifier] = identity
            (_, model), job_id = identity
            grouped.setdefault(model, set()).add(job_id)
            if model is ResearchVerificationJob:
                grouped.setdefault(TopicAnalysisJob, set()).add(job_id)
    target_fields = ("article_id", "source_id", "topic_id", "proposal_id")
    jobs = {
        model: _load_rows(session, model, ids, ("status", "outcome", *target_fields))
        for model, ids in grouped.items()
    }
    targets = (
        ("article_id", Article, "title"),
        ("source_id", Source, "name"),
        ("topic_id", Topic, "name"),
        ("proposal_id", TopicProposal, "proposed"),
    )
    subjects = {}
    for field, model, label in targets:
        ids = {
            value
            for rows in jobs.values()
            for row in rows.values()
            if (value := getattr(row, field, None))
        }
        subjects[model] = _load_rows(session, model, ids, (label,))
    for identifier, ((kind, model), job_id) in identities.items():
        job = jobs[model].get(job_id)
        if job is None:
            continue  # Completion/deletion between Redis and SQL reads is normal.
        result = results[identifier]
        result.id, result.kind, result.status = job_id, kind, job.status
        result.outcome = getattr(job, "outcome", None)
        target_job = jobs[TopicAnalysisJob].get(job_id) if kind == "research-verification" else job
        for field, target, label in targets:
            if target_id := getattr(target_job, field, None):
                setattr(result, field, target_id)
                row = subjects[target].get(target_id)
                if row is not None and result.target_name is None:
                    result.target_name = (
                        row.proposed.get("name") if label == "proposed" else getattr(row, label)
                    )
    return results


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
    pending = []
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
        ai = any(q in AI_QUEUES for q in queues)
        background = any(q in (*BACKGROUND_QUEUES, "notifications") for q in queues)
        worker = WorkerOut(
            name=key.removeprefix("rq:worker:"),
            queues=queues,
            role="solver"
            if queues == ["solver"]
            else "mixed"
            if ai and background
            else "ai"
            if ai
            else "background",
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
            pending.append((worker, fields["current_job"]))
        workers.append(worker)
    jobs = current_jobs(connection, session, [identifier for _, identifier in pending], now)
    for worker, identifier in pending:
        worker.current_job = jobs[identifier]
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
    # Combine aggregate round trips without changing per-table status semantics.
    statements = []
    for definition, name, condition in queue_lanes():
        model = definition.model
        statement = select(
            literal(name), model.status, func.count(), func.min(model.created_at)
        ).group_by(model.status)
        if condition is not None:
            statement = statement.where(condition)
        statements.append(statement)
    statements.append(
        select(
            literal("research-verification"),
            literal("review_required"),
            func.count(),
            func.min(ResearchVerificationJob.created_at),
        )
        .where(ResearchVerificationJob.outcome == "review_required")
        .group_by(ResearchVerificationJob.relationships)
    )
    for name, status, count, earliest in session.execute(union_all(*statements)):
        queue = queues[name]
        if status in {"queued", "running", "failed", "succeeded", "review_required"}:
            setattr(queue, status, getattr(queue, status) + count)
        if status == "queued" and earliest:
            queue.oldest_queued_at = min(queue.oldest_queued_at or earliest, earliest)
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
